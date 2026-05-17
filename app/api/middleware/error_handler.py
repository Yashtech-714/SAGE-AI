"""
app/api/middleware/error_handler.py
=====================================
Centralised exception handlers — ensures ALL errors return structured JSON.

WHY centralised error handling?
----------------------------------
Without this, FastAPI returns:
  - Pydantic validation errors as complex nested JSON (hard for clients to parse)
  - Unhandled exceptions as plain text HTML (breaks API contract)
  - Python exceptions leak internal details (security risk)

With centralised handlers, EVERY error response has this contract:
  {
    "error": "human-readable message",
    "error_type": "validation_error | server_error | ...",
    "request_id": "abc-123"
  }

This is the standard pattern in enterprise APIs (AWS API Gateway,
Google Cloud Endpoints, Azure APIM all enforce structured error responses).

HTTP Status Code Mapping:
  400 Bad Request       → invalid question format
  422 Unprocessable     → Pydantic validation failure
  429 Too Many Requests → rate limit (future)
  500 Internal Error    → unexpected server error
  503 Service Unavailable → LLM / DB down
"""

import traceback

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.core.logger import logger
from app.core.config import settings


def _request_id(request: Request) -> str:
    """Extract request ID from state (set by tracing middleware)."""
    return getattr(request.state, "request_id", "unknown")[:8]


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """
    Handle Pydantic request validation errors (422).

    Transforms Pydantic's verbose error format into a clean message:
      Before: [{"loc": ["body", "question"], "msg": "value is not a valid string", ...}]
      After:  {"error": "question: value is not a valid string", "error_type": "validation_error"}
    """
    errors = exc.errors()
    # Build a readable error message from Pydantic's structured errors
    messages = []
    for err in errors:
        loc = " -> ".join(str(l) for l in err["loc"] if l != "body")
        messages.append(f"{loc}: {err['msg']}" if loc else err["msg"])

    error_msg = "; ".join(messages)

    logger.warning(
        "REQUEST {id} | Validation error: {e}",
        id=_request_id(request),
        e=error_msg,
    )

    return JSONResponse(
        status_code=422,
        content={
            "error": error_msg,
            "error_type": "validation_error",
            "request_id": _request_id(request),
            "hint": "Check the /docs endpoint for the correct request schema.",
        },
    )


async def value_error_handler(
    request: Request,
    exc: ValueError,
) -> JSONResponse:
    """Handle explicit ValueError raises (400 Bad Request)."""
    logger.warning(
        "REQUEST {id} | Bad request: {e}",
        id=_request_id(request),
        e=str(exc)[:200],
    )
    return JSONResponse(
        status_code=400,
        content={
            "error": str(exc),
            "error_type": "bad_request",
            "request_id": _request_id(request),
        },
    )


async def generic_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """
    Catch-all for unhandled exceptions (500 Internal Server Error).

    In production mode: returns generic message (hides internals).
    In development mode: includes the traceback for debugging.

    This is the "fail safe" handler — it ensures the API always returns
    valid JSON even when something completely unexpected happens.
    """
    logger.error(
        "REQUEST {id} | Unhandled exception: {e}\n{tb}",
        id=_request_id(request),
        e=str(exc)[:200],
        tb=traceback.format_exc()[-1000:],
    )

    content: dict = {
        "error": "An internal error occurred. Please try again.",
        "error_type": "internal_server_error",
        "request_id": _request_id(request),
    }

    if not settings.is_production:
        # In development, include the actual error for debugging
        content["debug_error"] = str(exc)[:200]

    return JSONResponse(status_code=500, content=content)
