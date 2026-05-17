"""
app/api/routes/metrics.py
============================
GET /metrics — operational observability dashboard endpoint.

WHY a /metrics endpoint in an AI API?
----------------------------------------
AI systems have unique failure modes that standard web metrics miss:
  - High retry rate → model is hallucinating (prompts need improvement)
  - Low success rate → validator is too strict OR schema context is wrong
  - Rising avg tokens → prompts are growing (token cost increasing)
  - High pipeline time vs low DB time → LLM is the bottleneck (not DB)

These metrics help answer:
  - "Is the AI pipeline healthy?"
  - "How much does this cost per query?"
  - "Is the retry agent being triggered often?"
  - "Are prompts getting more expensive over time?"

Production upgrade path:
  In production, you'd expose /metrics in Prometheus text format and scrape
  it with a Prometheus server, then visualise in Grafana. Our MetricsStore
  structure is designed to make this migration trivial.
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_metrics
from app.core.config import settings
from app.core.metrics import MetricsStore

router = APIRouter(tags=["Observability"])


@router.get(
    "/metrics",
    summary="API operational metrics",
    description=(
        "Returns query counts, success rates, retry rates, "
        "latency statistics, and LLM token usage."
    ),
)
async def get_metrics_snapshot(
    metrics: MetricsStore = Depends(get_metrics),
) -> dict:
    """Return a complete operational metrics snapshot."""
    snapshot = metrics.snapshot()

    # Add system configuration context
    snapshot["system"] = {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "app_env": settings.app_env,
        "max_retries_configured": settings.max_query_retries,
        "max_result_rows": settings.max_result_rows,
    }

    return snapshot


@router.get(
    "/metrics/summary",
    summary="One-line metrics summary",
)
async def metrics_summary(
    metrics: MetricsStore = Depends(get_metrics),
) -> dict:
    """Quick health summary — total queries and success rate."""
    snap = metrics.snapshot()
    return {
        "total_queries": snap["queries"]["total"],
        "success_rate_pct": snap["queries"]["success_rate_pct"],
        "avg_pipeline_ms": snap["latency"]["avg_pipeline_ms"],
        "total_tokens_used": snap["tokens"]["total"],
        "retry_rate_pct": snap["reliability"]["retry_rate_pct"],
    }
