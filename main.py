"""
main.py
=========
FastAPI application factory and entry point.

This module wires together:
  - Application lifecycle (startup/shutdown via lifespan)
  - All API routers (query, health, schema, metrics, examples)
  - Middleware (request tracing, CORS)
  - Exception handlers (structured error responses)
  - OpenAPI documentation metadata

Run:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.core.app_state import lifespan
from app.core.config import settings
from app.api.middleware.logging_middleware import RequestTracingMiddleware
from app.api.middleware.error_handler import (
    validation_error_handler,
    value_error_handler,
    generic_error_handler,
)
from app.api.routes import query, health, schema, metrics, examples

# ── Application Factory ────────────────────────────────────────────────────────

app = FastAPI(
    title="NL2SQL Analytics Engine",
    description="""
## Natural Language to SQL AI System

An enterprise-grade analytics assistant that converts business questions
into optimised SQL queries and returns structured results with AI-generated insights.

### Architecture Highlights
- **Provider-agnostic LLM**: OpenAI, Groq, Grok, Together via unified interface
- **6-Layer SQL Safety Validator**: blocks injection, DDL, and hallucinated schemas
- **Self-Correcting Retry Agent**: reduces failure rate from ~12% to ~1-2%
- **Token-Efficient Prompting**: keyword-based context selection saves ~60% tokens
- **Full Observability**: request IDs, latency tracking, token usage metrics

### Dataset
Olist Brazilian E-Commerce (99k orders, 9 relational tables)
    """,
    version="3.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    contact={
        "name": "NL2SQL System",
        "email": "admin@nl2sql.dev",
    },
    license_info={
        "name": "MIT",
    },
)

# ── Middleware (order matters — applied bottom-up) ─────────────────────────────

# CORS: allow all origins in development (restrict in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else ["https://yourdomain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request tracing: adds X-Request-ID header and structured logs
app.add_middleware(RequestTracingMiddleware)

# ── Exception Handlers ─────────────────────────────────────────────────────────

app.add_exception_handler(RequestValidationError, validation_error_handler)   # type: ignore[arg-type]
app.add_exception_handler(ValueError, value_error_handler)                    # type: ignore[arg-type]
app.add_exception_handler(Exception, generic_error_handler)                   # type: ignore[arg-type]

# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(query.router)      # POST /query
app.include_router(health.router)     # GET  /health
app.include_router(schema.router)     # GET  /schema, /schema/tables, /schema/table/{name}
app.include_router(metrics.router)    # GET  /metrics, /metrics/summary
app.include_router(examples.router)   # GET  /examples


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Root"], summary="API info")
async def root() -> dict:
    """API root — returns links to all available endpoints."""
    return {
        "name": "NL2SQL Analytics Engine",
        "version": "3.0.0",
        "description": "Natural Language to SQL AI System for Olist E-Commerce Analytics",
        "endpoints": {
            "query":    "POST /query  — ask a business analytics question",
            "health":   "GET  /health — service readiness check",
            "schema":   "GET  /schema — database schema metadata",
            "metrics":  "GET  /metrics — operational metrics",
            "examples": "GET  /examples — sample questions",
            "docs":     "GET  /docs — interactive Swagger UI",
            "redoc":    "GET  /redoc — ReDoc documentation",
        },
        "provider": settings.llm_provider,
        "model":    settings.llm_model,
        "env":      settings.app_env,
    }
