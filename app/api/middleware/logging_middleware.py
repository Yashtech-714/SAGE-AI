"""
app/api/middleware/logging_middleware.py
==========================================
Request tracing middleware — adds a unique request ID to every request
and logs structured entry/exit with latency.

WHY request IDs matter in AI systems
---------------------------------------
AI pipelines are non-deterministic. When a user reports "my query failed",
you need to find THAT SPECIFIC request in logs — not just the endpoint or
timestamp. A request ID:

  - Correlates the API log entry with LLM call logs, retry logs, DB logs
  - Enables log aggregation (Datadog, Splunk) to reconstruct the full trace
  - Is returned in the response header (X-Request-ID) so clients can report it

This is the simplest form of distributed tracing. In production, you'd
replace this with OpenTelemetry spans which propagate across microservices.

WHY BaseHTTPMiddleware?
------------------------
BaseHTTPMiddleware is FastAPI's standard middleware base. It wraps every
request in a call_next() that executes the rest of the stack.

Alternatives:
  - @app.middleware("http") decorator -- same thing, less reusable
  - Starlette pure ASGI middleware -- more performant but more complex

For an analytics API with moderate traffic, BaseHTTPMiddleware is fine.
"""

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logger import logger


class RequestTracingMiddleware(BaseHTTPMiddleware):
    """
    Assigns a UUID request ID to every incoming request and logs:
      - Request start: method, path, client IP
      - Request end: status code, latency in ms

    The request ID is:
      - Added to request.state (available to all endpoint handlers)
      - Returned in the X-Request-ID response header
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())

        # Make request ID available inside endpoint handlers
        request.state.request_id = request_id

        start_time = time.perf_counter()

        logger.info(
            "REQUEST {id} | {method} {path} | client={client}",
            id=request_id[:8],           # short prefix for log readability
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            # Log unhandled exceptions before re-raising
            elapsed = round((time.perf_counter() - start_time) * 1000, 1)
            logger.error(
                "REQUEST {id} | UNHANDLED EXCEPTION | {t}ms | {exc}",
                id=request_id[:8],
                t=elapsed,
                exc=str(exc)[:100],
            )
            raise

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)

        logger.info(
            "RESPONSE {id} | {status} | {t}ms",
            id=request_id[:8],
            status=response.status_code,
            t=elapsed_ms,
        )

        # Attach request ID to response header for client correlation
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(elapsed_ms)

        return response
