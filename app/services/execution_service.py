"""
app/services/execution_service.py
=====================================
Async SQL execution pipeline.

Responsibilities:
  - Execute validated SQL against the SQLite database
  - Enforce the row-count limit (safety net)
  - Measure execution time (observability)
  - Serialise result rows to JSON-safe dicts
  - Provide structured error handling

WHY a dedicated execution service?
------------------------------------
Separating execution from validation and generation allows:
  1. Independent testing (test execution without mocking LLM)
  2. Swap databases trivially (SQLite -> PostgreSQL: change session, not this)
  3. Centralised row-limit enforcement (no code duplication)
  4. Single place to add query metrics (execution time, row count)

Execution vs. LLM errors
--------------------------
The validator catches ~90% of SQL problems before execution.
The remaining 10% are things the validator can't catch:
  - Correct table names but wrong column aliases in ORDER BY
  - HAVING clauses referencing non-aggregated columns
  - SQLite-specific functions used incorrectly (strftime format strings)
  - Division by zero in computed columns

These errors surface as SQLite exceptions and are captured here,
then returned to the retry agent with the exact DB error message.

Row serialisation
------------------
SQLite rows are returned as Row objects (not plain dicts).
We convert to `list[dict[str, Any]]` because:
  - Pydantic models can validate dicts, not Row objects
  - JSON serialisation works on dicts, not Row objects
  - Downstream code (explanation agent, API response) expects dicts
"""

import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.logger import logger
from app.db.session import engine
from app.schemas.query import QueryExecutionResult


class ExecutionService:
    """Executes validated SQL queries against the database."""

    def __init__(self, max_rows: int | None = None) -> None:
        self._max_rows = max_rows or settings.max_result_rows

    async def execute(
        self,
        sql: str,
        max_rows: int | None = None,
    ) -> QueryExecutionResult:
        """
        Execute a SQL query and return structured results.

        Args:
            sql:      The validated SELECT query (no trailing semicolon needed)
            max_rows: Override the service-level row cap for this request

        Returns:
            QueryExecutionResult with rows, columns, timing, and error info

        The row cap is applied at the Python level (slicing the result set)
        rather than rewriting SQL to add LIMIT, because:
          1. The LLM should already include LIMIT in its SQL
          2. This is a backstop, not the primary enforcement mechanism
          3. Rewriting SQL could break ORDER BY / LIMIT combinations
        """
        effective_max = max_rows or self._max_rows
        start_time = time.perf_counter()

        logger.info("ExecutionService: executing SQL ({n} chars)", n=len(sql))
        logger.debug("SQL:\n{sql}", sql=sql)

        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(sql))
                columns = list(result.keys())

                # Fetch all and apply row cap
                all_rows = result.fetchall()
                capped   = len(all_rows) > effective_max
                rows     = all_rows[:effective_max]

                # Serialise: Row -> dict[str, Any]
                row_dicts: list[dict[str, Any]] = [
                    dict(zip(columns, row)) for row in rows
                ]

                elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

                if capped:
                    logger.warning(
                        "ExecutionService: result capped | returned={r} | "
                        "total={t} | limit={l}",
                        r=len(rows), t=len(all_rows), l=effective_max,
                    )

                logger.info(
                    "ExecutionService: success | rows={r} | cols={c} | time={t}ms",
                    r=len(row_dicts),
                    c=len(columns),
                    t=elapsed_ms,
                )

                return QueryExecutionResult(
                    rows=row_dicts,
                    columns=columns,
                    row_count=len(row_dicts),
                    execution_time_ms=elapsed_ms,
                    was_capped=capped,
                    error=None,
                )

        except SQLAlchemyError as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            error_msg  = self._clean_db_error(str(e))

            logger.error(
                "ExecutionService: DB error after {t}ms: {e}",
                t=elapsed_ms, e=error_msg,
            )

            return QueryExecutionResult(
                rows=[],
                columns=[],
                row_count=0,
                execution_time_ms=elapsed_ms,
                was_capped=False,
                error=error_msg,
            )

        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error("ExecutionService: unexpected error: {e}", e=str(e))

            return QueryExecutionResult(
                rows=[],
                columns=[],
                row_count=0,
                execution_time_ms=elapsed_ms,
                was_capped=False,
                error=f"Execution error: {e}",
            )

    @staticmethod
    def _clean_db_error(raw_error: str) -> str:
        """
        Extract the human-readable portion of a SQLAlchemy error.

        SQLAlchemy wraps DB errors with its own message prefix.
        We extract the SQLite error message which is what the retry
        agent needs to understand what went wrong.

        Example raw: "(sqlite3.OperationalError) no such column: total_revenue"
        Cleaned:     "no such column: total_revenue"
        """
        # Extract the part after the first ") "
        if ") " in raw_error:
            return raw_error.split(") ", 1)[1].strip()
        return raw_error[:300]  # Truncate very long SQLAlchemy messages
