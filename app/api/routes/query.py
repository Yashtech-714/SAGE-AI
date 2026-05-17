"""
app/api/routes/query.py
=========================
POST /query — the primary AI endpoint.

This is the complete end-to-end AI Text-to-SQL pipeline exposed as an HTTP API.

Request -> Response Flow:
  1. Pydantic validates the incoming JSON (automatic, by FastAPI)
  2. SQLAgent runs the full pipeline (context → LLM → validate → execute → retry)
  3. ExplanationAgent generates the business insight
  4. Metrics are updated asynchronously
  5. Structured QueryResponse is returned

Design Decisions
-----------------
ASYNC endpoint:
  `async def query()` means FastAPI runs this in the asyncio event loop.
  Since all downstream calls (DB, LLM API) are awaitable, the event loop
  never blocks. 50 concurrent requests are handled without thread contention.

Request ID propagation:
  The request_id from the tracing middleware is threaded through the
  response and logs, enabling full request tracing across the AI pipeline.

Graceful failure:
  The endpoint NEVER raises an exception to the user. SQLAgent returns a
  structured QueryResponse with success=False on failure. The HTTP status
  code is 200 even on AI failure — the structured response contains the
  error field. This is the standard pattern for AI API backends (OpenAI,
  Anthropic, and Gemini all return 200 with structured errors for AI-level
  failures, reserving 4xx/5xx for protocol/auth failures).
"""

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.agents.explanation_agent import ExplanationAgent
from app.agents.sql_agent import SQLAgent
from app.api.dependencies import (
    get_explanation_agent,
    get_metrics,
    get_sql_agent,
)
from app.core.logger import logger
from app.core.metrics import MetricsStore
from app.schemas.query import NLQueryRequest, QueryExecutionResult, QueryResponse

router = APIRouter(prefix="/query", tags=["Query"])

# ── Sample questions for the examples endpoint (also used in docs) ────────────
EXAMPLE_QUESTIONS = [
    {
        "question": "Which sellers generated the highest total revenue?",
        "max_rows": 10,
        "category": "Revenue Analysis",
        "complexity": "multi-join",
    },
    {
        "question": "Show monthly order count and revenue trends for 2018",
        "max_rows": 12,
        "category": "Time Series",
        "complexity": "aggregation",
    },
    {
        "question": "What is the average review score by product category?",
        "max_rows": 20,
        "category": "Customer Satisfaction",
        "complexity": "multi-join + aggregation",
    },
    {
        "question": "Which customer states have the most orders?",
        "max_rows": 15,
        "category": "Geographic Analysis",
        "complexity": "simple aggregation",
    },
    {
        "question": "What are the top 10 products by total freight cost?",
        "max_rows": 10,
        "category": "Logistics",
        "complexity": "join + aggregation",
    },
    {
        "question": "Which payment methods are most popular by order count?",
        "max_rows": 10,
        "category": "Payments",
        "complexity": "simple aggregation",
    },
    {
        "question": "What is the average delivery time in days by seller state?",
        "max_rows": 27,
        "category": "Delivery Performance",
        "complexity": "date arithmetic + multi-join",
    },
    {
        "question": "Which product categories have the lowest average review scores?",
        "max_rows": 15,
        "category": "Product Quality",
        "complexity": "multi-join + aggregation",
    },
]


@router.post(
    "",
    response_model=QueryResponse,
    summary="Ask a business analytics question",
    description="""
Submit a natural language business question. The AI engine will:
1. Build schema context from the live database
2. Generate optimised SQL using an LLM
3. Validate the SQL through 6 safety layers
4. Execute the query against the Olist e-commerce database
5. Self-correct and retry if the SQL is invalid
6. Return results with a business insight explanation

**Example questions:**
- "Which sellers generated the highest revenue?"
- "Show monthly sales trends for 2018"
- "What is the average review score by product category?"
""",
    responses={
        200: {"description": "Query processed (check 'success' field for AI-level result)"},
        422: {"description": "Invalid request format"},
        500: {"description": "Unexpected server error"},
    },
)
async def run_query(
    body: NLQueryRequest,
    request: Request,
    agent: SQLAgent = Depends(get_sql_agent),
    explanation_agent: ExplanationAgent = Depends(get_explanation_agent),
    metrics: MetricsStore = Depends(get_metrics),
) -> QueryResponse:
    """End-to-end AI Text-to-SQL pipeline."""

    request_id = getattr(request.state, "request_id", "unknown")
    pipeline_start = time.perf_counter()

    logger.info(
        "QUERY {id} | question='{q}' | max_rows={r}",
        id=request_id[:8],
        q=body.question[:80],
        r=body.max_rows,
    )

    # ── Run the full AI pipeline ──────────────────────────────────────────────
    result: QueryResponse = await agent.generate_and_execute(
        question=body.question,
        max_rows=body.max_rows,
    )

    # ── Generate business insight (if query succeeded and requested) ─────────
    insight: str | None = None
    if result.success and body.include_insight and result.rows:
        exec_result = QueryExecutionResult(
            rows=result.rows,
            columns=result.columns,
            row_count=result.row_count,
            execution_time_ms=result.execution_time_ms,
            was_capped=result.was_capped,
        )
        insight = await explanation_agent.explain(
            question=body.question,
            sql=result.sql,
            exec_result=exec_result,
        )

    # ── Attach insight to result ──────────────────────────────────────────────
    result.insight = insight

    # ── Record metrics ────────────────────────────────────────────────────────
    pipeline_ms = round((time.perf_counter() - pipeline_start) * 1000, 2)
    await metrics.record_query(
        success=result.success,
        attempts=result.attempts,
        execution_time_ms=result.execution_time_ms,
        pipeline_time_ms=pipeline_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        validation_failed=not result.success and result.attempts == 1,
        execution_error=not result.success and result.attempts > 1,
    )

    logger.info(
        "QUERY {id} | success={s} | rows={r} | attempts={a} | "
        "pipeline={p}ms | tokens={t}",
        id=request_id[:8],
        s=result.success,
        r=result.row_count,
        a=result.attempts,
        p=pipeline_ms,
        t=result.total_tokens,
    )

    return result
