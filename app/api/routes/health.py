"""
app/api/routes/health.py
===========================
GET /health — service readiness probe.

Enterprise health check design
---------------------------------
Production systems use health endpoints for:
  1. Kubernetes liveness probes  → "is the process alive?"
  2. Kubernetes readiness probes → "is it ready to accept traffic?"
  3. Load balancer health checks → "should I route to this instance?"
  4. Monitoring dashboards       → "what's the service status?"

Our /health endpoint checks:
  - Database connectivity (can we execute a query?)
  - LLM connectivity (is the API reachable?)
  - Schema loaded (are services initialised?)
  - Configuration (is an API key present?)

HTTP Status Code Contract:
  200 → fully healthy (ready to serve traffic)
  503 → degraded or unavailable (stop routing traffic here)

The 503 on DB failure is critical for Kubernetes: if the DB is down,
the pod should be marked "NotReady" and removed from the service mesh.
"""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logger import logger
from app.db.session import engine
from sqlalchemy import text

router = APIRouter(tags=["Infrastructure"])


@router.get(
    "/health",
    summary="Service health check",
    description="Checks database connectivity, LLM availability, and service readiness.",
)
async def health_check(request: Request) -> JSONResponse:
    """
    Comprehensive health probe.

    Performs live checks rather than returning cached status,
    because stale health status is worse than no health check.
    """
    checks: dict[str, dict] = {}
    overall_healthy = True

    # ── Check 1: Database connectivity ───────────────────────────────────────
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM orders"))
            order_count = result.scalar()
        checks["database"] = {
            "status": "ok",
            "detail": f"Connected | orders table: {order_count:,} rows",
        }
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)[:100]}
        overall_healthy = False
        logger.error("Health check: DB error: {e}", e=str(e))

    # ── Check 2: Schema loaded ────────────────────────────────────────────────
    schema_meta = getattr(getattr(request.app, "state", None), "schema_meta", None)
    if schema_meta and schema_meta.total_tables > 0:
        checks["schema"] = {
            "status": "ok",
            "detail": f"{schema_meta.total_tables} tables loaded",
        }
    else:
        checks["schema"] = {"status": "error", "detail": "Schema not loaded"}
        overall_healthy = False

    # ── Check 3: LLM connectivity ────────────────────────────────────────────
    llm_provider = getattr(getattr(request.app, "state", None), "llm_provider", None)
    if llm_provider:
        try:
            healthy = await llm_provider.health_check()
            checks["llm"] = {
                "status": "ok" if healthy else "degraded",
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "detail": "reachable" if healthy else "health check failed",
            }
            if not healthy:
                overall_healthy = False
        except Exception as e:
            checks["llm"] = {
                "status": "error",
                "provider": settings.llm_provider,
                "detail": str(e)[:80],
            }
            overall_healthy = False
    else:
        checks["llm"] = {"status": "not_initialised"}

    # ── Check 4: Configuration ───────────────────────────────────────────────
    checks["config"] = {
        "status": "ok",
        "app_env": settings.app_env,
        "api_key_configured": settings.has_api_key,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
    }

    http_status = status.HTTP_200_OK if overall_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_status,
        content={
            "status": "healthy" if overall_healthy else "degraded",
            "checks": checks,
        },
    )
