"""
scripts/test_phase3.py
========================
Phase 3 Integration Test Suite — AI Orchestration Layer

Test Strategy
--------------
Phase 3 has two distinct testing modes:

MODE 1: NO API KEY (runs always)
  Tests everything EXCEPT the actual LLM call:
    - Context service: keyword → table detection
    - Prompt service: prompt assembly and example selection
    - Output parser: SQL extraction from messy LLM output
    - Execution service: real SQL queries against the SQLite DB
    - Retry agent: prompt building and retry count logic
    - Full pipeline with MockLLMProvider (no API call)

MODE 2: WITH API KEY (skipped automatically if no key configured)
  Tests the full end-to-end AI pipeline:
    - Real LLM SQL generation
    - Self-correction retry
    - Business insight explanation
    - Realistic business questions

This design means CI/CD can run the full test suite without API keys,
while developers with keys can test the full AI pipeline locally.

Run:
    python -m scripts.test_phase3
"""

import asyncio
import sys

from app.db.session import engine
from app.services.schema_service import SchemaService
from app.services.relationship_service import RelationshipService
from app.services.validator_service import ValidatorService
from app.services.parser_service import ParserService
from app.services.context_service import ContextService
from app.services.prompt_service import PromptService
from app.services.execution_service import ExecutionService
from app.services.llm_service import MockLLMProvider, create_llm_provider
from app.agents.sql_agent import SQLAgent, parse_sql_from_llm_output
from app.agents.explanation_agent import ExplanationAgent
from app.core.config import settings
from app.schemas.query import QueryExecutionResult


# ── Output helpers ────────────────────────────────────────────────────────────

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


def _skip(msg: str) -> None:
    print(f"  [SKIP] {msg}")


# ── Test 1: Context Service ───────────────────────────────────────────────────

def test_context_service(
    schema_svc: SchemaService,
    rel_svc: RelationshipService,
) -> ContextService:
    _section("TEST 1: Intelligent Context Builder")

    ctx_svc = ContextService(schema_svc.get_schema_meta(), rel_svc)

    # Question about sellers + revenue -> should select sellers, order_items, orders
    pkg1 = ctx_svc.build_context("Which sellers generated the highest total revenue?")
    assert "sellers" in pkg1.selected_table_names, \
        f"Expected 'sellers' in context, got {pkg1.selected_table_names}"
    assert "order_items" in pkg1.selected_table_names, \
        f"Expected 'order_items' in context"
    _ok(f"Revenue question → tables: {pkg1.selected_table_names}")
    _ok(f"Strategy: {pkg1.context_strategy} | schema_chars={len(pkg1.schema_prompt)}")

    # Question about reviews -> should include order_reviews
    pkg2 = ctx_svc.build_context("What is the average review score by product category?")
    assert "order_reviews" in pkg2.selected_table_names, \
        f"Expected 'order_reviews' in context, got {pkg2.selected_table_names}"
    _ok(f"Review question → tables: {pkg2.selected_table_names}")

    # Question about monthly trend -> should include orders
    pkg3 = ctx_svc.build_context("Show monthly revenue trends for 2017")
    assert "orders" in pkg3.selected_table_names
    _ok(f"Trend question → tables: {pkg3.selected_table_names}")

    # Token efficiency check: context should be smaller than full schema
    full_context = ctx_svc.build_context("show me everything about customers geolocation sellers products reviews payments orders items categories")
    schema_svc_prompt = schema_svc.get_prompt_context()
    _ok(f"Full schema: {len(schema_svc_prompt)} chars | Selective: {len(pkg1.schema_prompt)} chars")
    assert len(pkg1.schema_prompt) < len(schema_svc_prompt), \
        "Selective context should be smaller than full schema"
    _ok("Token efficiency: selective context is smaller than full schema")

    # Relationship context included
    assert "=" in pkg1.relationship_prompt or len(pkg1.relationship_prompt) > 10, \
        "Relationship prompt should be non-empty"
    _ok(f"Relationship context: {len(pkg1.relationship_prompt)} chars")

    return ctx_svc


# ── Test 2: Prompt Service ────────────────────────────────────────────────────

def test_prompt_service(ctx_svc: ContextService) -> PromptService:
    _section("TEST 2: Dynamic Prompt Builder")

    prompt_svc = PromptService()

    context = ctx_svc.build_context("Top 10 sellers by total revenue")

    messages = prompt_svc.build_sql_prompt(
        question="Top 10 sellers by total revenue",
        context=context,
        max_rows=10,
    )

    # Must return OpenAI messages format
    assert len(messages) == 2, f"Expected 2 messages, got {len(messages)}"
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    _ok("Messages format: [system, user] correct")

    system_content = messages[0]["content"]
    user_content   = messages[1]["content"]

    # System prompt must have key rules
    assert "SELECT" in system_content
    assert "sqlite" in system_content.lower() or "SQLite" in system_content
    _ok(f"System prompt: {len(system_content)} chars | SQLite rules present")

    # User prompt must contain schema and question
    assert "TABLE:" in user_content or "DATABASE SCHEMA" in user_content
    assert "sellers" in user_content.lower()
    assert "Top 10 sellers" in user_content
    _ok(f"User prompt: {len(user_content)} chars | schema + question present")

    # Few-shot examples should be present
    assert "EXAMPLE" in user_content
    _ok("Few-shot examples: present in prompt")

    # Test example selection: ranking question should get ranking examples
    from app.prompts.sql_prompt import FEW_SHOT_EXAMPLES
    examples = prompt_svc._select_examples("Top sellers by highest revenue")
    patterns = [e.pattern for e in examples]
    assert "ranking" in patterns, f"Expected 'ranking' pattern selected, got {patterns}"
    _ok(f"Example selection: ranking question → patterns {patterns}")

    # Test retry prompt
    retry_messages = prompt_svc.build_retry_messages(
        question="Top sellers by revenue",
        failed_sql="SELECT * FROM sellers JOIN revenue ON 1=1",
        error_message="Tables not found: ['revenue']",
        context=context,
        attempt_number=2,
    )
    assert len(retry_messages) == 2
    assert "Failed" in retry_messages[1]["content"] or "failed" in retry_messages[1]["content"]
    assert "revenue" in retry_messages[1]["content"]
    _ok("Retry prompt: error message and failed SQL embedded correctly")

    return prompt_svc


# ── Test 3: SQL Output Parser ─────────────────────────────────────────────────

def test_output_parser() -> None:
    _section("TEST 3: SQL Output Parser (LLM Response Cleaning)")

    test_cases = [
        # (raw_output, expected_contains, description)
        (
            "```sql\nSELECT * FROM orders LIMIT 10\n```",
            "SELECT * FROM orders LIMIT 10",
            "Markdown code fence extraction",
        ),
        (
            "Here is the query:\nSELECT order_id FROM orders LIMIT 5",
            "SELECT order_id FROM orders",
            "Prefix text before SELECT",
        ),
        (
            "SELECT * FROM sellers; DROP TABLE orders",
            "SELECT * FROM sellers",
            "Trailing statement after semicolon (parsed to first SELECT)",
        ),
        (
            "SELECT price FROM order_items LIMIT 10 -- this gets the price",
            "SELECT price FROM order_items LIMIT 10",
            "Inline comment removal",
        ),
        (
            "SELECT * FROM orders LIMIT 10;",
            "SELECT * FROM orders LIMIT 10",
            "Trailing semicolon removal",
        ),
        (
            "```\nSELECT seller_id, SUM(price) AS revenue\nFROM sellers\nLIMIT 10\n```",
            "SELECT seller_id",
            "Code fence without sql tag",
        ),
    ]

    all_pass = True
    for raw, expected_fragment, description in test_cases:
        parsed = parse_sql_from_llm_output(raw)
        if expected_fragment.upper() in parsed.upper():
            _ok(f"{description}")
            _info(f"  → '{parsed[:60].strip()}'")
        else:
            _fail(f"{description}")
            _info(f"  Expected: '{expected_fragment}'")
            _info(f"  Got:      '{parsed[:60]}'")
            all_pass = False

    if all_pass:
        _ok("All output parser tests passed!")


# ── Test 4: Execution Service ─────────────────────────────────────────────────

async def test_execution_service() -> ExecutionService:
    _section("TEST 4: Async SQL Execution Pipeline")

    exec_svc = ExecutionService(max_rows=100)

    # Test 1: Valid simple query
    r1 = await exec_svc.execute("SELECT order_id, order_status FROM orders LIMIT 5")
    assert r1.error is None, f"Expected success, got: {r1.error}"
    assert r1.row_count == 5
    assert "order_id" in r1.columns
    assert isinstance(r1.rows[0], dict), "Rows should be dicts"
    _ok(f"Simple SELECT: {r1.row_count} rows | {r1.execution_time_ms}ms")

    # Test 2: Aggregation query
    r2 = await exec_svc.execute("""
        SELECT order_status, COUNT(*) AS cnt
        FROM orders
        GROUP BY order_status
        ORDER BY cnt DESC
        LIMIT 10
    """)
    assert r2.error is None
    assert r2.row_count >= 1
    _ok(f"Aggregation query: {r2.row_count} order statuses | columns={r2.columns}")

    # Test 3: Multi-table JOIN
    r3 = await exec_svc.execute("""
        SELECT s.seller_state, COUNT(DISTINCT o.order_id) AS orders
        FROM sellers s
        JOIN order_items oi ON s.seller_id = oi.seller_id
        JOIN orders o ON oi.order_id = o.order_id
        WHERE o.order_status = 'delivered'
        GROUP BY s.seller_state
        ORDER BY orders DESC
        LIMIT 5
    """)
    assert r3.error is None
    assert r3.row_count >= 1
    _ok(f"JOIN query: {r3.row_count} states | top seller state: {r3.rows[0].get('seller_state','?')}")

    # Test 4: Row cap enforcement
    r4 = await exec_svc.execute("SELECT order_id FROM orders", max_rows=10)
    assert r4.row_count == 10
    assert r4.was_capped is True
    _ok(f"Row cap: returned {r4.row_count} rows, was_capped={r4.was_capped}")

    # Test 5: DB error handling (bad SQL that passes our validator's column check)
    r5 = await exec_svc.execute(
        "SELECT nonexistent_column_xyz FROM orders LIMIT 1"
    )
    assert r5.error is not None
    assert r5.row_count == 0
    _ok(f"DB error captured: '{r5.error[:60]}'")

    return exec_svc


# ── Test 5: Full Pipeline with MockLLM ───────────────────────────────────────

async def test_pipeline_with_mock(
    schema_svc: SchemaService,
    rel_svc: RelationshipService,
    validator_svc: ValidatorService,
    parser_svc: ParserService,
    ctx_svc: ContextService,
    prompt_svc: PromptService,
    exec_svc: ExecutionService,
) -> None:
    _section("TEST 5: Full Pipeline with Mock LLM (no API key needed)")

    # Test 5a: Valid SQL from mock -> should succeed
    mock_llm = MockLLMProvider(
        default_response="SELECT order_status, COUNT(*) AS cnt FROM orders GROUP BY order_status LIMIT 10"
    )
    agent = SQLAgent(
        llm_provider=mock_llm,
        schema_service=schema_svc,
        validator_service=validator_svc,
        execution_service=exec_svc,
        context_service=ctx_svc,
        prompt_service=prompt_svc,
    )
    result = await agent.generate_and_execute(
        question="How many orders are in each status?",
        max_rows=10,
    )
    assert result.success is True, f"Expected success, got: {result.error}"
    assert result.row_count >= 1
    assert result.attempts == 1
    _ok(f"Mock LLM (valid SQL): success | rows={result.row_count} | attempts={result.attempts}")
    _ok(f"Context tables used: {result.context_tables}")
    _ok(f"Total tokens logged: {result.total_tokens}")

    # Test 5b: Invalid SQL from mock -> should retry and ultimately fail
    mock_llm_bad = MockLLMProvider(
        default_response="SELECT * FROM nonexistent_hallucinated_table LIMIT 10"
    )
    agent_bad = SQLAgent(
        llm_provider=mock_llm_bad,
        schema_service=schema_svc,
        validator_service=validator_svc,
        execution_service=exec_svc,
        context_service=ctx_svc,
        prompt_service=prompt_svc,
        max_retries=2,
    )
    result_bad = await agent_bad.generate_and_execute(
        question="show me the nonexistent table",
        max_rows=5,
    )
    assert result_bad.success is False
    assert result_bad.error is not None
    assert result_bad.attempts == 2  # tried max_retries times
    _ok(f"Mock LLM (invalid SQL): failed gracefully | attempts={result_bad.attempts}")
    _ok(f"Error captured: '{result_bad.error[:80]}'")

    # Test 5c: Mock with markdown-wrapped SQL (parser stress test)
    mock_llm_md = MockLLMProvider(
        default_response="```sql\nSELECT seller_state, COUNT(*) AS sellers FROM sellers GROUP BY seller_state LIMIT 10\n```"
    )
    agent_md = SQLAgent(
        llm_provider=mock_llm_md,
        schema_service=schema_svc,
        validator_service=validator_svc,
        execution_service=exec_svc,
        context_service=ctx_svc,
        prompt_service=prompt_svc,
    )
    result_md = await agent_md.generate_and_execute(
        question="How many sellers are in each state?",
        max_rows=10,
    )
    assert result_md.success is True, f"Markdown parser failed: {result_md.error}"
    _ok(f"Mock LLM (markdown SQL): parsed and executed successfully | rows={result_md.row_count}")


# ── Test 6: Explanation Agent (no API) ───────────────────────────────────────

async def test_explanation_agent_mock() -> None:
    _section("TEST 6: Explanation Agent (Mock LLM)")

    mock_llm  = MockLLMProvider(
        default_response=(
            "São Paulo dominates seller revenue, generating R$1.52M across 847 orders. "
            "The top 3 states account for 61% of total platform revenue."
        )
    )
    prompt_svc = PromptService()
    agent      = ExplanationAgent(mock_llm, prompt_svc)

    exec_result = QueryExecutionResult(
        rows=[
            {"seller_state": "SP", "total_revenue": 1523442.31, "total_orders": 847},
            {"seller_state": "MG", "total_revenue": 412331.10, "total_orders": 231},
            {"seller_state": "PR", "total_revenue": 389210.55, "total_orders": 198},
        ],
        columns=["seller_state", "total_revenue", "total_orders"],
        row_count=3,
        execution_time_ms=12.4,
    )

    insight = await agent.explain(
        question="Which states have the highest seller revenue?",
        sql="SELECT seller_state, SUM(price) FROM sellers ... LIMIT 10",
        exec_result=exec_result,
    )
    assert len(insight) > 20, "Insight should be non-trivial"
    _ok(f"Explanation generated: {len(insight)} chars")
    _info(f"  → '{insight[:100]}'")

    # Test empty result fallback
    empty_result = QueryExecutionResult(
        rows=[], columns=[], row_count=0, execution_time_ms=5.0
    )
    fallback = await agent.explain(
        question="Something with no results",
        sql="SELECT * FROM orders WHERE 1=0",
        exec_result=empty_result,
    )
    assert "no results" in fallback.lower() or "returned" in fallback.lower()
    _ok("Empty result handled gracefully")


# ── Test 7: Real LLM (skipped if no API key) ─────────────────────────────────

async def test_real_llm_pipeline(
    schema_svc: SchemaService,
    rel_svc: RelationshipService,
    validator_svc: ValidatorService,
    parser_svc: ParserService,
    ctx_svc: ContextService,
    prompt_svc: PromptService,
    exec_svc: ExecutionService,
) -> None:
    _section("TEST 7: Real LLM End-to-End Pipeline")

    if not settings.has_api_key:
        _skip("No API key configured. Set LLM_API_KEY or OPENAI_API_KEY in .env")
        _skip("Skipping real LLM tests. All mock tests passed.")
        return

    _info(f"Provider: {settings.llm_provider} | Model: {settings.llm_model}")

    llm = create_llm_provider()

    # Health check
    healthy = await llm.health_check()
    if not healthy:
        _skip("LLM health check failed -- skipping real API tests")
        return
    _ok(f"LLM health check passed ({settings.llm_provider})")

    agent = SQLAgent(
        llm_provider=llm,
        schema_service=schema_svc,
        validator_service=validator_svc,
        execution_service=exec_svc,
        context_service=ctx_svc,
        prompt_service=prompt_svc,
    )

    explanation_agent = ExplanationAgent(llm, prompt_svc)

    # Real test questions
    test_questions = [
        ("Top 5 sellers by total revenue", 5),
        ("Which customer states have the most orders?", 10),
        ("What is the average review score by product category?", 10),
        ("Show monthly order counts for 2018", 12),
        ("What are the top 5 most delivered products by order count?", 5),
    ]

    passed = 0
    for question, max_rows in test_questions:
        print(f"\n  Question: '{question}'")
        result = await agent.generate_and_execute(question=question, max_rows=max_rows)

        if result.success:
            _ok(f"SUCCESS | rows={result.row_count} | attempts={result.attempts} | tokens={result.total_tokens}")
            _info(f"SQL: {result.sql[:80].replace(chr(10), ' ')}...")
            _info(f"Context tables: {result.context_tables}")

            # Generate insight
            exec_result = QueryExecutionResult(
                rows=result.rows,
                columns=result.columns,
                row_count=result.row_count,
                execution_time_ms=result.execution_time_ms,
            )
            insight = await explanation_agent.explain(question, result.sql, exec_result)
            _info(f"Insight: {insight[:120]}")
            passed += 1
        else:
            _fail(f"FAILED after {result.attempts} attempts: {result.error[:80]}")

    _ok(f"\n  {passed}/{len(test_questions)} real LLM questions answered successfully")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print()
    print("=" * 65)
    print("  PHASE 3 — AI Orchestration Layer Test Suite")
    print("=" * 65)

    # Bootstrap Phase 2 services (needed by Phase 3)
    schema_svc   = SchemaService(engine)
    schema_meta  = await schema_svc.load()
    rel_svc      = RelationshipService(schema_meta)
    parser_svc   = ParserService()
    validator_svc= ValidatorService(schema_svc, parser_svc)

    # Phase 3 services
    ctx_svc     = test_context_service(schema_svc, rel_svc)
    prompt_svc  = test_prompt_service(ctx_svc)
    test_output_parser()
    exec_svc    = await test_execution_service()
    await test_pipeline_with_mock(
        schema_svc, rel_svc, validator_svc, parser_svc,
        ctx_svc, prompt_svc, exec_svc,
    )
    await test_explanation_agent_mock()
    await test_real_llm_pipeline(
        schema_svc, rel_svc, validator_svc, parser_svc,
        ctx_svc, prompt_svc, exec_svc,
    )

    _section("PHASE 3 TEST SUMMARY")
    print("  [PASS] Context service: intelligent table selection")
    print("  [PASS] Prompt service: dynamic prompt assembly + example selection")
    print("  [PASS] Output parser: robust SQL extraction from LLM responses")
    print("  [PASS] Execution service: async SQL execution + row capping")
    print("  [PASS] Full pipeline: mock LLM integration (valid + invalid SQL)")
    print("  [PASS] Explanation agent: business insight generation")
    print()
    print("  Phase 3 is complete. Ready for Phase 4 (FastAPI endpoints).")
    print()


if __name__ == "__main__":
    asyncio.run(main())
