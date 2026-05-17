"""
app/api/dependencies.py
=========================
FastAPI dependency injection — shared service accessors.

WHY Depends() over direct imports?
-------------------------------------
Direct import of a service singleton couples the endpoint function to a
specific implementation. Depends() decouples them:

  - Tests can override any dependency with a mock in one line:
      app.dependency_overrides[get_sql_agent] = lambda: MockSQLAgent()

  - Different environments can inject different implementations
    (e.g. a read-only agent in production, a verbose agent in staging)

  - FastAPI logs dependency resolution, making request tracing easier

All dependency functions are sync (no async needed — they just read
app.state, which is already initialised at startup).
"""

from fastapi import Request

from app.agents.sql_agent import SQLAgent
from app.agents.explanation_agent import ExplanationAgent
from app.core.metrics import MetricsStore
from app.services.schema_service import SchemaService
from app.schemas.query import SchemaMeta


def get_sql_agent(request: Request) -> SQLAgent:
    """Return the application-wide SQL agent."""
    return request.app.state.sql_agent


def get_explanation_agent(request: Request) -> ExplanationAgent:
    """Return the application-wide explanation agent."""
    return request.app.state.explanation_agent


def get_metrics(request: Request) -> MetricsStore:
    """Return the application-wide metrics store."""
    return request.app.state.metrics


def get_schema_service(request: Request) -> SchemaService:
    """Return the application-wide schema service."""
    return request.app.state.schema_svc


def get_schema_meta(request: Request) -> SchemaMeta:
    """Return the cached schema metadata."""
    return request.app.state.schema_meta
