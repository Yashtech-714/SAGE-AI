"""
scripts/test_phase4.py
========================
Phase 4 Integration Test Suite — FastAPI API Layer

Test Strategy
--------------
Uses httpx.AsyncClient with ASGITransport to test the full FastAPI
application in-process (no network required).

This is the recommended approach for FastAPI testing:
  - Full stack test (middleware, routes, error handlers all run)
  - No mock HTTP server needed
  - Fast execution (~10ms per request)
  - Tests the real app.state and dependency injection

Lifespan Note:
  httpx's ASGITransport does NOT auto-trigger FastAPI's lifespan.
  We manually enter app.router.lifespan_context to initialise app.state
  before running requests, then exit it after all tests complete.
  This mirrors how pytest-anyio's lifespan fixture works.

Run:
    $env:PYTHONIOENCODING="utf-8"; python -m scripts.test_phase4
"""

import asyncio
import json

import httpx
from httpx import AsyncClient, ASGITransport

from main import app
from app.services.llm_service import MockLLMProvider
from app.agents.sql_agent import SQLAgent
from app.agents.explanation_agent import ExplanationAgent
from app.services.prompt_service import PromptService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print()
    print("=" * 65)
    print(f"  {title}")
    print("=" * 65)


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"         {msg}")


# ── App client fixture ────────────────────────────────────────────────────────

async def get_test_client() -> AsyncClient:
    """
    Create an httpx AsyncClient that talks directly to the FastAPI app.

    After the lifespan startup runs, we swap the LLM provider with a mock
    so tests don't make real API calls.
    """
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client


# ── Test 1: Application Startup ───────────────────────────────────────────────

async def test_startup(client: AsyncClient) -> None:
    _section("TEST 1: Application Startup & Root Endpoint")

    r = await client.get("/")
    assert r.status_code == 200, f"Root returned {r.status_code}"
    data = r.json()

    assert "name" in data
    assert "endpoints" in data
    assert "POST /query" in data["endpoints"].get("query", "")
    _ok(f"Root endpoint: {data['name']} v{data.get('version', '?')}")
    _ok(f"Provider: {data.get('provider')} | Model: {data.get('model')}")
    _ok(f"Endpoints listed: {list(data['endpoints'].keys())}")

    # Verify X-Request-ID header on every response
    assert "x-request-id" in r.headers
    assert len(r.headers["x-request-id"]) == 36  # UUID format
    _ok(f"X-Request-ID header present: {r.headers['x-request-id'][:8]}...")

    # Verify X-Response-Time-Ms
    assert "x-response-time-ms" in r.headers
    _ok(f"X-Response-Time-Ms: {r.headers['x-response-time-ms']}ms")


# ── Test 2: Health Endpoint ───────────────────────────────────────────────────

async def test_health(client: AsyncClient) -> None:
    _section("TEST 2: Health Check Endpoint")

    r = await client.get("/health")
    # Allow 200 or 503 (503 if LLM check fails, which is OK in test env)
    assert r.status_code in (200, 503), f"Unexpected status: {r.status_code}"

    data = r.json()
    assert "status" in data
    assert "checks" in data

    checks = data["checks"]
    _ok(f"Overall status: {data['status']}")

    # Database must always be healthy (SQLite is always available)
    assert "database" in checks
    db_status = checks["database"]["status"]
    assert db_status == "ok", f"Database check failed: {checks['database']}"
    _ok(f"Database: {db_status} | {checks['database'].get('detail', '')[:60]}")

    # Schema must be loaded
    assert "schema" in checks
    assert checks["schema"]["status"] == "ok"
    _ok(f"Schema: {checks['schema'].get('detail')}")

    # Config must be present
    assert "config" in checks
    _ok(f"Config: env={checks['config'].get('app_env')} | "
        f"provider={checks['config'].get('provider')}")

    # LLM check may fail (no real API in test) but must be reported
    if "llm" in checks:
        _ok(f"LLM: {checks['llm'].get('status')} ({checks['llm'].get('provider', '?')})")


# ── Test 3: Schema Endpoints ──────────────────────────────────────────────────

async def test_schema(client: AsyncClient) -> None:
    _section("TEST 3: Schema Endpoints")

    # /schema — full schema
    r = await client.get("/schema")
    assert r.status_code == 200
    data = r.json()
    assert data["total_tables"] == 9
    assert len(data["tables"]) == 9
    assert len(data["relationships"]) >= 8
    _ok(f"GET /schema: {data['total_tables']} tables, {data['relationship_count']} relationships")

    # Verify table structure
    first_table = data["tables"][0]
    assert "table" in first_table
    assert "columns" in first_table
    assert "primary_keys" in first_table
    _ok(f"First table: {first_table['table']} ({len(first_table['columns'])} columns)")

    # /schema/tables
    r2 = await client.get("/schema/tables")
    assert r2.status_code == 200
    tables_data = r2.json()
    assert tables_data["total"] == 9
    assert "orders" in tables_data["tables"]
    assert "sellers" in tables_data["tables"]
    _ok(f"GET /schema/tables: {tables_data['tables']}")

    # /schema/table/{name} — valid table
    r3 = await client.get("/schema/table/orders")
    assert r3.status_code == 200
    orders_data = r3.json()
    assert orders_data["table"] == "orders"
    col_names = [c["name"] for c in orders_data["columns"]]
    assert "order_id" in col_names
    assert "order_status" in col_names
    _ok(f"GET /schema/table/orders: {len(col_names)} columns")

    # /schema/table/{name} — non-existent table
    r4 = await client.get("/schema/table/nonexistent_table")
    assert r4.status_code == 404
    _ok("GET /schema/table/nonexistent: 404 returned correctly")


# ── Test 4: Examples Endpoint ─────────────────────────────────────────────────

async def test_examples(client: AsyncClient) -> None:
    _section("TEST 4: Examples Endpoint")

    r = await client.get("/examples")
    assert r.status_code == 200
    data = r.json()

    assert data["total_examples"] >= 5
    assert len(data["categories"]) >= 3
    assert len(data["all_questions"]) >= 5
    _ok(f"GET /examples: {data['total_examples']} examples across {len(data['categories'])} categories")
    _ok(f"Categories: {data['categories']}")
    _info(f"Sample: '{data['all_questions'][0]}'")


# ── Test 5: Metrics Endpoint ──────────────────────────────────────────────────

async def test_metrics(client: AsyncClient) -> None:
    _section("TEST 5: Metrics Endpoint")

    r = await client.get("/metrics")
    assert r.status_code == 200
    data = r.json()

    assert "queries" in data
    assert "reliability" in data
    assert "latency" in data
    assert "tokens" in data
    assert "system" in data
    _ok(f"GET /metrics: all metric categories present")
    _ok(f"System: provider={data['system']['llm_provider']} | model={data['system']['llm_model']}")

    r2 = await client.get("/metrics/summary")
    assert r2.status_code == 200
    summary = r2.json()
    assert "total_queries" in summary
    assert "success_rate_pct" in summary
    _ok(f"GET /metrics/summary: {summary}")


# ── Test 6: POST /query — Mock LLM ────────────────────────────────────────────

async def test_query_with_mock(client: AsyncClient) -> None:
    _section("TEST 6: POST /query with Mock LLM")

    # Inject mock LLM into app state (bypasses real API calls)
    valid_sql = "SELECT order_status, COUNT(*) AS cnt FROM orders GROUP BY order_status ORDER BY cnt DESC LIMIT 10"
    mock_llm = MockLLMProvider(default_response=valid_sql)
    prompt_svc = PromptService()

    # Get existing services from app.state and rebuild agent with mock LLM
    original_agent = app.state.sql_agent
    app.state.sql_agent = SQLAgent(
        llm_provider=mock_llm,
        schema_service=app.state.schema_svc,
        validator_service=app.state.validator_svc,
        execution_service=original_agent._executor,
        context_service=original_agent._context,
        prompt_service=prompt_svc,
    )
    app.state.explanation_agent = ExplanationAgent(
        MockLLMProvider(default_response="Orders are distributed across 8 status types, with 'delivered' being the most common at 96.5%."),
        prompt_svc,
    )

    try:
        # Test 6a: Valid query
        payload = {"question": "How many orders are in each status?", "max_rows": 10}
        r = await client.post("/query", json=payload)
        assert r.status_code == 200
        data = r.json()

        assert data["success"] is True
        assert data["row_count"] >= 1
        assert data["attempts"] == 1
        assert len(data["columns"]) >= 1
        assert len(data["sql"]) > 10
        assert "context_tables" in data
        _ok(f"Valid query: success | rows={data['row_count']} | attempts={data['attempts']}")
        _ok(f"SQL generated: {data['sql'][:60].replace(chr(10), ' ')}...")
        _ok(f"Context tables: {data['context_tables']}")
        _ok(f"Tokens: prompt={data['prompt_tokens']} + completion={data['completion_tokens']}")
        if data.get("insight"):
            _info(f"Insight: {data['insight'][:100]}")

        # Verify X-Request-ID in response headers
        assert "x-request-id" in r.headers
        _ok(f"Request-ID: {r.headers['x-request-id'][:8]}...")

        # Test 6b: Query with insight disabled
        payload2 = {"question": "Show order counts", "max_rows": 5, "include_insight": False}
        r2 = await client.post("/query", json=payload2)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["insight"] is None
        _ok("include_insight=False: insight correctly omitted")

        # Test 6c: Hallucinated SQL from mock → retry → fail gracefully
        bad_mock = MockLLMProvider(default_response="SELECT * FROM fake_hallucinated_table LIMIT 5")
        app.state.sql_agent = SQLAgent(
            llm_provider=bad_mock,
            schema_service=app.state.schema_svc,
            validator_service=app.state.validator_svc,
            execution_service=original_agent._executor,
            context_service=original_agent._context,
            prompt_service=prompt_svc,
            max_retries=2,
        )
        payload3 = {"question": "Test hallucination", "max_rows": 5}
        r3 = await client.post("/query", json=payload3)
        assert r3.status_code == 200
        data3 = r3.json()
        assert data3["success"] is False
        assert data3["error"] is not None
        assert data3["attempts"] == 2
        _ok(f"Hallucinated SQL: failed gracefully | attempts={data3['attempts']}")
        _info(f"Error: {data3['error'][:80]}")

    finally:
        # Restore original agent
        app.state.sql_agent = original_agent


# ── Test 7: Request Validation ────────────────────────────────────────────────

async def test_request_validation(client: AsyncClient) -> None:
    _section("TEST 7: Request Validation & Error Handling")

    # Empty question
    r1 = await client.post("/query", json={"question": "", "max_rows": 10})
    assert r1.status_code == 422
    data1 = r1.json()
    assert "error" in data1
    assert "error_type" in data1
    assert data1["error_type"] == "validation_error"
    _ok(f"Empty question: 422 | error_type={data1['error_type']}")

    # Question too short
    r2 = await client.post("/query", json={"question": "hi", "max_rows": 10})
    assert r2.status_code == 422
    _ok(f"Question too short: 422")

    # max_rows out of range
    r3 = await client.post("/query", json={"question": "Show all orders", "max_rows": 9999})
    assert r3.status_code == 422
    _ok(f"max_rows=9999 (over limit): 422")

    # max_rows = 0
    r4 = await client.post("/query", json={"question": "Show all orders", "max_rows": 0})
    assert r4.status_code == 422
    _ok(f"max_rows=0: 422")

    # Missing question field
    r5 = await client.post("/query", json={"max_rows": 10})
    assert r5.status_code == 422
    _ok(f"Missing question field: 422")

    # Wrong content type (plain text instead of JSON)
    r6 = await client.post("/query", content="hello", headers={"Content-Type": "text/plain"})
    assert r6.status_code == 422
    _ok(f"Wrong content type: 422")


# ── Test 8: Metrics Update After Queries ─────────────────────────────────────

async def test_metrics_update(client: AsyncClient) -> None:
    _section("TEST 8: Metrics Update After Queries")

    # Get baseline
    r_before = await client.get("/metrics")
    baseline = r_before.json()
    queries_before = baseline["queries"]["total"]

    # Run a query with mock
    valid_sql = "SELECT seller_state, COUNT(*) as c FROM sellers GROUP BY seller_state LIMIT 5"
    mock = MockLLMProvider(default_response=valid_sql)
    prompt_svc = PromptService()
    original = app.state.sql_agent
    app.state.sql_agent = SQLAgent(
        llm_provider=mock,
        schema_service=app.state.schema_svc,
        validator_service=app.state.validator_svc,
        execution_service=original._executor,
        context_service=original._context,
        prompt_service=prompt_svc,
    )

    try:
        await client.post("/query", json={"question": "Sellers by state", "max_rows": 5})
        r_after = await client.get("/metrics")
        after = r_after.json()
        queries_after = after["queries"]["total"]

        assert queries_after == queries_before + 1, \
            f"Expected {queries_before + 1} queries, got {queries_after}"
        _ok(f"Metrics updated: total queries {queries_before} -> {queries_after}")
        _ok(f"Success rate: {after['queries']['success_rate_pct']}%")
        _ok(f"Avg pipeline: {after['latency']['avg_pipeline_ms']}ms")
    finally:
        app.state.sql_agent = original



# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print()
    print("=" * 65)
    print("  PHASE 4 -- FastAPI API Layer Test Suite")
    print("=" * 65)

    # httpx's ASGITransport does NOT auto-trigger FastAPI's lifespan.
    # We manually enter the lifespan context so app.state is fully populated,
    # then create the AsyncClient for requests.
    async with app.router.lifespan_context(app):
        client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        async with client:
            await test_startup(client)
            await test_health(client)
            await test_schema(client)
            await test_examples(client)
            await test_metrics(client)
            await test_query_with_mock(client)
            await test_request_validation(client)
            await test_metrics_update(client)

    _section("PHASE 4 TEST SUMMARY")
    print("  [PASS] Application startup: lifespan, services, app.state")
    print("  [PASS] GET /health: DB, schema, LLM, config checks")
    print("  [PASS] GET /schema: full schema + table lookup + 404 handling")
    print("  [PASS] GET /examples: categorised question library")
    print("  [PASS] GET /metrics + /metrics/summary: operational dashboard")
    print("  [PASS] POST /query: valid SQL, insight, hallucination handling")
    print("  [PASS] Request validation: 422 on all invalid inputs")
    print("  [PASS] Metrics update: counters increment correctly after queries")
    print()
    print("  Phase 4 complete. API is production-ready.")
    print("  Run: uvicorn main:app --reload")
    print("  Docs: http://localhost:8000/docs")
    print()


if __name__ == "__main__":
    asyncio.run(main())

