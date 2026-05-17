"""
app/core/metrics.py
=====================
In-memory metrics store for the AI query pipeline.

WHY in-memory metrics?
------------------------
Production systems use time-series databases (Prometheus, InfluxDB, Datadog).
For a portfolio/demo system, in-memory metrics give us:
  - Zero external dependencies
  - Instant startup
  - All the same SURFACE API as Prometheus counters
  - Easy to swap: just replace MetricsStore with a Prometheus client call

The metrics we track mirror real production AI API dashboards:
  - total_queries / success_rate  → SLA monitoring
  - retry_rate                    → model quality signal
  - avg_execution_time_ms         → latency SLA
  - total_tokens                  → cost monitoring
  - validation_failures           → safety layer effectiveness

WHY dataclass + asyncio.Lock?
------------------------------
FastAPI endpoints run concurrently on the asyncio event loop.
Without a lock, two requests can read-modify-write counters simultaneously,
causing lost updates (classic race condition).

asyncio.Lock is non-blocking (doesn't pause the event loop) unlike
threading.Lock, which would defeat the purpose of async.
"""

import time
from dataclasses import dataclass, field
from asyncio import Lock


@dataclass
class MetricsStore:
    """Thread-safe (asyncio-safe) in-memory metrics accumulator."""

    # ── Counters ────────────────────────────────────────────────────────────
    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0
    total_retries: int = 0          # sum of (attempts - 1) across all queries
    validation_failures: int = 0
    execution_errors: int = 0

    # ── Timing accumulators ─────────────────────────────────────────────────
    total_execution_time_ms: float = 0.0   # DB execution time only
    total_pipeline_time_ms: float = 0.0    # end-to-end (incl. LLM calls)

    # ── LLM cost tracking ────────────────────────────────────────────────────
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0

    # ── Metadata ─────────────────────────────────────────────────────────────
    started_at: float = field(default_factory=time.time)

    # ── Internal lock ────────────────────────────────────────────────────────
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    async def record_query(
        self,
        *,
        success: bool,
        attempts: int,
        execution_time_ms: float,
        pipeline_time_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        validation_failed: bool = False,
        execution_error: bool = False,
    ) -> None:
        """Record one completed query pipeline run."""
        async with self._lock:
            self.total_queries += 1

            if success:
                self.successful_queries += 1
            else:
                self.failed_queries += 1

            retries = max(0, attempts - 1)
            self.total_retries += retries

            if validation_failed:
                self.validation_failures += 1

            if execution_error:
                self.execution_errors += 1

            self.total_execution_time_ms += execution_time_ms
            self.total_pipeline_time_ms  += pipeline_time_ms
            self.total_prompt_tokens     += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_tokens            += prompt_tokens + completion_tokens

    def snapshot(self) -> dict:
        """Return a JSON-serialisable snapshot of current metrics."""
        uptime_s = round(time.time() - self.started_at, 1)

        success_rate = (
            round(self.successful_queries / self.total_queries * 100, 1)
            if self.total_queries > 0 else 0.0
        )
        retry_rate = (
            round(self.total_retries / self.total_queries * 100, 1)
            if self.total_queries > 0 else 0.0
        )
        avg_pipeline_ms = (
            round(self.total_pipeline_time_ms / self.total_queries, 1)
            if self.total_queries > 0 else 0.0
        )
        avg_execution_ms = (
            round(self.total_execution_time_ms / self.total_queries, 1)
            if self.total_queries > 0 else 0.0
        )
        avg_tokens = (
            round(self.total_tokens / self.total_queries)
            if self.total_queries > 0 else 0
        )

        return {
            "uptime_seconds": uptime_s,
            "queries": {
                "total": self.total_queries,
                "successful": self.successful_queries,
                "failed": self.failed_queries,
                "success_rate_pct": success_rate,
            },
            "reliability": {
                "total_retries": self.total_retries,
                "retry_rate_pct": retry_rate,
                "validation_failures": self.validation_failures,
                "execution_errors": self.execution_errors,
            },
            "latency": {
                "avg_pipeline_ms": avg_pipeline_ms,
                "avg_db_execution_ms": avg_execution_ms,
                "total_pipeline_ms": round(self.total_pipeline_time_ms, 1),
            },
            "tokens": {
                "total": self.total_tokens,
                "prompt": self.total_prompt_tokens,
                "completion": self.total_completion_tokens,
                "avg_per_query": avg_tokens,
            },
        }
