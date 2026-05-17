"""
app/agents/sql_agent.py
=========================
Core SQL generation agent -- the main orchestrator.

This is the central AI pipeline coordinator. It wires together every
service from Phases 2 and 3 into a single, clean workflow.

Workflow
---------
    question
        → ContextService   (select relevant schema)
        → PromptService    (assemble LLM prompt)
        → LLMService       (generate SQL)
        → OutputParser     (extract clean SQL from LLM response)
        → ValidatorService (6-layer safety check)
        → ExecutionService (run against DB)
        → [RetryAgent if failed]
        → ExplanationAgent (business insight)
        → QueryResponse

Design Principles
------------------
1. SINGLE RESPONSIBILITY: SQLAgent only orchestrates. Each sub-task
   (context selection, prompt building, validation, execution) lives
   in its own service. SQLAgent calls them, it doesn't implement them.

2. DEPENDENCY INJECTION: all services are injected at construction time,
   not instantiated inside methods. This makes the agent testable with
   mock services (no real DB or API calls needed in unit tests).

3. STRUCTURED OBSERVABILITY: every significant step logs the full context
   (question, SQL, token count, timing, attempt number). This is essential
   for debugging AI pipelines where failures are non-deterministic.

Output Parser Design
---------------------
LLMs are trained to be helpful and verbose. Even with strict instructions,
they sometimes produce:
  - SQL wrapped in markdown: ```sql SELECT ... ```
  - Explanation before SQL: "Here is the query: SELECT ..."
  - Trailing comments: SELECT ... -- this query returns...

The parser handles all these cases robustly. This is critical for
production reliability: a brittle parser that fails on any LLM
variability will break the pipeline for real users.
"""

import re
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logger import logger
from app.schemas.query import QueryResponse, ValidationResult
from app.services.context_service import ContextService, ContextPackage
from app.services.execution_service import ExecutionService
from app.services.llm_service import LLMProvider, LLMResponse
from app.services.prompt_service import PromptService
from app.services.schema_service import SchemaService
from app.services.validator_service import ValidatorService


# ── SQL Output Parser ─────────────────────────────────────────────────────────

_MD_FENCE_RE = re.compile(r'```(?:sql)?\s*(.*?)\s*```', re.DOTALL | re.IGNORECASE)
_SELECT_RE   = re.compile(r'(SELECT\s+.+)', re.DOTALL | re.IGNORECASE)


def parse_sql_from_llm_output(raw: str) -> str:
    """
    Robustly extract clean SQL from LLM output.

    Handles:
      1. Markdown fenced code blocks  (```sql SELECT ... ```)
      2. Inline fences                (` SELECT ... `)
      3. Prefix text                  ("Here is the SQL: SELECT ...")
      4. Trailing inline comments     (SELECT ... -- explanation)
      5. Extra whitespace / newlines
      6. Trailing semicolons          (SQLite doesn't need them; they cause
                                      issues in multi-statement detection)

    WHY robustness matters here:
      Production LLMs, even with "output ONLY SQL" instructions, sometimes
      add prefix text on the first token (before the instruction fully kicks in).
      A brittle parser that only handles the happy path will fail ~5-10% of
      the time in production, creating a poor user experience.
    """
    text = raw.strip()

    # 1. Extract from markdown code fence (highest priority -- most explicit)
    fence_match = _MD_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    # 2. Find the SELECT statement (skips any prefix explanation)
    select_match = _SELECT_RE.search(text)
    if select_match:
        text = select_match.group(1).strip()

    # 3. Remove inline trailing comments (keep only the SQL part)
    # We split on -- only if it's outside quotes (simplified: just take before --)
    # This is safe because our validator will catch any issues
    if " -- " in text:
        text = text.split(" -- ")[0].strip()

    # 4. Remove trailing semicolon
    text = text.rstrip(";").strip()

    return text


# ── SQL Generation Attempt Record ─────────────────────────────────────────────

@dataclass
class SQLAttempt:
    """Records everything about one SQL generation attempt."""
    attempt_number: int
    raw_llm_output: str
    parsed_sql: str
    validation_result: ValidationResult | None = None
    execution_error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    succeeded: bool = False


# ── SQL Agent ─────────────────────────────────────────────────────────────────

class SQLAgent:
    """
    The main AI orchestration engine.

    Accepts a natural language question and returns a complete QueryResponse
    including the generated SQL, query results, and business insight.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        schema_service: SchemaService,
        validator_service: ValidatorService,
        execution_service: ExecutionService,
        context_service: ContextService,
        prompt_service: PromptService,
        max_retries: int | None = None,
    ) -> None:
        self._llm       = llm_provider
        self._schema    = schema_service
        self._validator = validator_service
        self._executor  = execution_service
        self._context   = context_service
        self._prompter  = prompt_service
        self._max_retries = max_retries or settings.max_query_retries

    async def generate_and_execute(
        self,
        question: str,
        max_rows: int = 50,
    ) -> QueryResponse:
        """
        Full pipeline: NL question → validated SQL → query results → insight.

        Returns a QueryResponse regardless of success/failure.
        Failure cases populate the error field, not raise exceptions,
        so the API layer always gets a structured response.
        """
        pipeline_start = time.perf_counter()

        logger.info(
            "SQLAgent: pipeline start | question='{q}' | max_rows={r}",
            q=question[:80],
            r=max_rows,
        )

        # ── Step 1: Build context ─────────────────────────────────────────────
        context = self._context.build_context(question)

        # ── Step 2: Initial SQL generation ───────────────────────────────────
        messages  = self._prompter.build_sql_prompt(question, context, max_rows)
        llm_resp  = await self._llm.complete(
            messages=messages,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        parsed_sql = parse_sql_from_llm_output(llm_resp.content)

        attempt = SQLAttempt(
            attempt_number=1,
            raw_llm_output=llm_resp.content,
            parsed_sql=parsed_sql,
            prompt_tokens=llm_resp.prompt_tokens,
            completion_tokens=llm_resp.completion_tokens,
        )

        logger.info(
            "SQLAgent: attempt 1 | parsed_sql='{sql}'",
            sql=parsed_sql[:120].replace("\n", " "),
        )

        # ── Step 3: Validate ──────────────────────────────────────────────────
        validation = self._validator.validate(parsed_sql)
        attempt.validation_result = validation

        if not validation.is_valid:
            logger.warning(
                "SQLAgent: attempt 1 validation failed | layer={l} | error={e}",
                l=validation.failed_layer.value if validation.failed_layer else "?",
                e=validation.error[:100] if validation.error else "",
            )
            # Hand off to retry agent
            return await self._retry_pipeline(
                question=question,
                context=context,
                max_rows=max_rows,
                attempts=[attempt],
                last_error=validation.error or "Validation failed",
                pipeline_start=pipeline_start,
            )

        # ── Step 4: Execute ───────────────────────────────────────────────────
        exec_result = await self._executor.execute(parsed_sql, max_rows)
        attempt.succeeded = not bool(exec_result.error)
        attempt.execution_error = exec_result.error

        if exec_result.error:
            logger.warning(
                "SQLAgent: attempt 1 execution failed | error={e}",
                e=exec_result.error[:100],
            )
            return await self._retry_pipeline(
                question=question,
                context=context,
                max_rows=max_rows,
                attempts=[attempt],
                last_error=exec_result.error,
                pipeline_start=pipeline_start,
            )

        # ── Step 5: Build response ────────────────────────────────────────────
        elapsed = round((time.perf_counter() - pipeline_start) * 1000, 2)
        total_tokens = llm_resp.total_tokens

        logger.info(
            "SQLAgent: SUCCESS on attempt 1 | rows={r} | time={t}ms | tokens={tk}",
            r=exec_result.row_count,
            t=elapsed,
            tk=total_tokens,
        )

        return QueryResponse(
            question=question,
            sql=parsed_sql,
            rows=exec_result.rows,
            columns=exec_result.columns,
            row_count=exec_result.row_count,
            execution_time_ms=exec_result.execution_time_ms,
            total_time_ms=elapsed,
            attempts=1,
            prompt_tokens=llm_resp.prompt_tokens,
            completion_tokens=llm_resp.completion_tokens,
            total_tokens=total_tokens,
            model_used=llm_resp.model,
            context_tables=context.selected_table_names,
            was_capped=exec_result.was_capped,
            error=None,
            success=True,
        )

    async def _retry_pipeline(
        self,
        question: str,
        context: ContextPackage,
        max_rows: int,
        attempts: list[SQLAttempt],
        last_error: str,
        pipeline_start: float,
    ) -> QueryResponse:
        """
        Delegated to the retry agent module.
        We import here to avoid circular imports.
        """
        from app.agents.retry_agent import RetryAgent
        retry = RetryAgent(
            llm_provider=self._llm,
            validator=self._validator,
            executor=self._executor,
            prompter=self._prompter,
            max_retries=self._max_retries,
        )
        return await retry.retry(
            question=question,
            context=context,
            max_rows=max_rows,
            attempts=attempts,
            last_error=last_error,
            pipeline_start=pipeline_start,
        )
