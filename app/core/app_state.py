"""
app/core/app_state.py
========================
Application lifecycle management — service initialisation and teardown.

WHY FastAPI Lifespan over @app.on_event?
------------------------------------------
`@app.on_event("startup")` was deprecated in FastAPI 0.93+.
The `lifespan` context manager is the modern approach because:
  1. Startup AND shutdown code live in the same function (no split logic)
  2. Works correctly with pytest-asyncio (event loop shared properly)
  3. Explicit yield makes the startup/teardown sequence obvious
  4. Supports async context managers for external resources (DB pools, etc.)

WHY store services in app.state?
-----------------------------------
The alternative is module-level globals. app.state is better because:
  - Services are scoped to ONE app instance (multiple apps can coexist in tests)
  - app.state is thread-safe (FastAPI manages access)
  - Dependency injection via request.app.state is explicit and testable

Singleton pattern:
  All services are initialised ONCE at startup and reused across all requests.
  SchemaService loads and caches the full schema graph — this takes ~200ms
  but happens only once. Subsequent requests pay 0ms for schema lookup.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.core.config import settings
from app.core.logger import logger
from app.core.metrics import MetricsStore
from app.services.schema_service import SchemaService
from app.services.relationship_service import RelationshipService
from app.services.parser_service import ParserService
from app.services.validator_service import ValidatorService
from app.services.context_service import ContextService
from app.services.prompt_service import PromptService
from app.services.execution_service import ExecutionService
from app.services.llm_service import create_llm_provider, MockLLMProvider
from app.agents.sql_agent import SQLAgent
from app.agents.explanation_agent import ExplanationAgent
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage application startup and shutdown.

    All heavy initialisation (DB introspection, service wiring) happens here.
    By the time the first request arrives, every service is warm and cached.

    This is the FastAPI equivalent of Spring Boot @PostConstruct or
    Django's AppConfig.ready().
    """
    logger.info("=" * 60)
    logger.info("NL2SQL AI System starting up...")
    logger.info("Provider: {p} | Model: {m}", p=settings.llm_provider, m=settings.llm_model)
    logger.info("=" * 60)

    # ── Phase 2 services ─────────────────────────────────────────────────────
    schema_svc    = SchemaService(engine)
    schema_meta   = await schema_svc.load()
    rel_svc       = RelationshipService(schema_meta)
    parser_svc    = ParserService()
    validator_svc = ValidatorService(schema_svc, parser_svc)

    logger.info("Schema loaded: {n} tables", n=schema_meta.total_tables)

    # ── Phase 3 services ─────────────────────────────────────────────────────
    ctx_svc    = ContextService(schema_meta, rel_svc)
    prompt_svc = PromptService()
    exec_svc   = ExecutionService()

    # LLM Provider
    if settings.has_api_key:
        llm_provider = create_llm_provider()
        logger.info("LLM provider: {p} ({m})", p=settings.llm_provider, m=settings.llm_model)
    else:
        llm_provider = MockLLMProvider()
        logger.warning("No API key configured — using MockLLMProvider (queries will return dummy SQL)")

    sql_agent = SQLAgent(
        llm_provider=llm_provider,
        schema_service=schema_svc,
        validator_service=validator_svc,
        execution_service=exec_svc,
        context_service=ctx_svc,
        prompt_service=prompt_svc,
    )

    explanation_agent = ExplanationAgent(llm_provider, prompt_svc)
    metrics           = MetricsStore()

    # ── Store in app.state (accessible from every request) ───────────────────
    app.state.schema_svc       = schema_svc
    app.state.schema_meta      = schema_meta
    app.state.rel_svc          = rel_svc
    app.state.validator_svc    = validator_svc
    app.state.sql_agent        = sql_agent
    app.state.explanation_agent= explanation_agent
    app.state.llm_provider     = llm_provider
    app.state.metrics          = metrics

    logger.info("All services initialised. API is ready.")

    yield  # ← Server is running here

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("NL2SQL shutting down...")
    await engine.dispose()
    logger.info("Database connections closed.")
