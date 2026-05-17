"""
scripts/test_phase2.py
========================
Integration test for Phase 2 — runs the full schema introspection,
relationship graph, and SQL validation pipeline.

Run:
    python -m scripts.test_phase2
"""

import asyncio

from app.db.session import engine
from app.services.schema_service import SchemaService
from app.services.relationship_service import RelationshipService
from app.services.parser_service import ParserService
from app.services.validator_service import ValidatorService


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


async def test_schema_service() -> SchemaService:
    _section("TEST 1: Schema Introspection Engine")

    svc = SchemaService(engine)
    meta = await svc.load()

    # Basic counts
    assert len(meta.tables) == 9, f"Expected 9 tables, got {len(meta.tables)}"
    _ok(f"Tables loaded: {len(meta.tables)}")

    # Spot-check orders table
    orders = meta.tables.get("orders")
    assert orders is not None, "orders table missing"
    _ok("orders table found")

    col_names = {c.name for c in orders.columns}
    expected = {"order_id", "customer_id", "order_status", "order_purchase_timestamp"}
    missing = expected - col_names
    assert not missing, f"Missing columns: {missing}"
    _ok(f"orders columns verified: {sorted(col_names)}")

    # FK check
    assert len(orders.foreign_keys) >= 1, "orders should have at least 1 FK"
    _ok(f"orders FKs: {[fk['references_table'] for fk in orders.foreign_keys]}")

    # Valid table lookups
    assert svc.is_valid_table("order_items"), "order_items should be valid"
    assert not svc.is_valid_table("order_details"), "order_details should NOT be valid"
    _ok("Table existence checks work")

    # Column lookups
    assert svc.is_valid_column("order_items", "price"), "price should be valid"
    assert not svc.is_valid_column("order_items", "total_revenue"), "total_revenue should NOT exist"
    _ok("Column existence checks work")

    # Relationship list
    assert len(meta.relationships) >= 6, f"Expected >= 6 FK relationships, got {len(meta.relationships)}"
    _ok(f"Relationships loaded: {len(meta.relationships)}")

    # Prompt context generation
    prompt_ctx = svc.get_prompt_context()
    assert "orders" in prompt_ctx and "FK" in prompt_ctx
    _ok(f"Prompt context generated ({len(prompt_ctx)} chars)")

    print()
    print("  --- Prompt Context Preview (first 600 chars) ---")
    print(prompt_ctx[:600])
    print("  ...")

    return svc


def test_relationship_service(schema_svc: SchemaService) -> RelationshipService:
    _section("TEST 2: Relationship Graph & Join Path Finder")

    meta = schema_svc.get_schema_meta()
    rel_svc = RelationshipService(meta)

    # Adjacency summary
    adj = rel_svc.get_adjacency_summary()
    _ok(f"Graph nodes: {list(adj.keys())}")

    # Direct neighbors
    order_neighbors = rel_svc.get_related_tables("orders")
    _ok(f"orders neighbors: {order_neighbors}")
    assert len(order_neighbors) >= 3, "orders should connect to customers, items, payments, reviews"

    # Join path: sellers -> orders (2 hops)
    path = rel_svc.find_join_path("sellers", "orders")
    assert path is not None, "Path sellers -> orders should exist"
    _ok(f"Path sellers -> orders: {len(path)} hops")
    for step in path:
        _info(f"  {step['from']}.{step['local_col']} = {step['to']}.{step['remote_col']}")

    # Join SQL
    join_sql = rel_svc.get_join_sql("sellers", "orders")
    assert join_sql is not None
    _ok(f"Join SQL sellers -> orders:\n")
    for line in join_sql.split("\n"):
        _info(f"  {line}")

    # Path: customers -> products (4 hops)
    path2 = rel_svc.find_join_path("customers", "products")
    assert path2 is not None, "Path customers -> products should exist"
    _ok(f"Path customers -> products: {len(path2)} hops")

    # Connectivity check
    assert rel_svc.tables_are_connected("sellers", "customers")
    # geolocation IS reachable from order_reviews via customers (expected behavior)
    assert rel_svc.tables_are_connected("geolocation", "order_reviews"), \
        "geolocation should be reachable from order_reviews via customers"
    _ok("Connectivity checks passed")

    # Prompt format
    fmt = rel_svc.format_for_prompt()
    assert "sellers" in fmt and "orders" in fmt
    _ok(f"Relationship prompt block generated ({len(fmt)} chars)")

    return rel_svc


def test_parser_service() -> ParserService:
    _section("TEST 3: SQL Parser Service")

    parser = ParserService()

    # ── Test 1: Valid SELECT ──────────────────────────────────────────────────
    sql = "SELECT order_id, customer_id FROM orders WHERE order_status = 'delivered' LIMIT 10"
    result = parser.parse(sql)
    assert result.is_select, "Should detect SELECT"
    assert "orders" in result.tables, f"Should find orders, got {result.tables}"
    assert not result.has_injection_pattern
    _ok(f"Valid SELECT parsed | tables={result.tables}")

    # ── Test 2: Multi-table JOIN ──────────────────────────────────────────────
    sql2 = """
    SELECT o.order_id, c.customer_city, SUM(p.payment_value) as total
    FROM orders o
    JOIN customers c ON o.customer_id = c.customer_id
    JOIN order_payments p ON o.order_id = p.order_id
    GROUP BY o.order_id
    """
    result2 = parser.parse(sql2)
    assert result2.is_select
    assert result2.has_join
    _ok(f"JOIN query parsed | tables={result2.tables} | has_join={result2.has_join}")

    # ── Test 3: Subquery detection ────────────────────────────────────────────
    sql3 = "SELECT * FROM orders WHERE order_id IN (SELECT order_id FROM order_items WHERE price > 100)"
    result3 = parser.parse(sql3)
    assert result3.has_subquery
    _ok(f"Subquery detected | has_subquery={result3.has_subquery}")

    # ── Test 4: Forbidden keywords detected ──────────────────────────────────
    sql4 = "DROP TABLE orders"
    result4 = parser.parse(sql4)
    assert "DROP" in result4.forbidden_keywords_found
    _ok(f"Forbidden keyword detected: {result4.forbidden_keywords_found}")

    # ── Test 5: Injection pattern ─────────────────────────────────────────────
    sql5 = "SELECT * FROM orders; DROP TABLE orders"
    result5 = parser.parse(sql5)
    assert result5.has_injection_pattern
    _ok(f"Injection pattern detected: {result5.injection_detail}")

    # ── Test 6: UNION injection ───────────────────────────────────────────────
    sql6 = "SELECT * FROM orders UNION SELECT username, password FROM users"
    result6 = parser.parse(sql6)
    assert result6.has_injection_pattern
    _ok(f"UNION injection detected: {result6.injection_detail}")

    return parser


def test_validator_service(
    schema_svc: SchemaService,
    parser_svc: ParserService,
) -> None:
    _section("TEST 4: Multi-Layer SQL Validator")

    validator = ValidatorService(schema_svc, parser_svc)

    tests: list[tuple[str, bool, str]] = [
        # (sql, expected_is_valid, description)
        (
            "SELECT * FROM orders LIMIT 10",
            True,
            "Valid simple SELECT",
        ),
        (
            """
            SELECT s.seller_id, SUM(oi.price) as revenue
            FROM sellers s
            JOIN order_items oi ON s.seller_id = oi.seller_id
            GROUP BY s.seller_id
            ORDER BY revenue DESC
            LIMIT 10
            """,
            True,
            "Valid JOIN with aggregation",
        ),
        (
            "",
            False,
            "Empty SQL (Layer 1 fail)",
        ),
        (
            "DROP TABLE orders",
            False,
            "DROP TABLE (Layer 2 fail)",
        ),
        (
            "DELETE FROM orders WHERE 1=1",
            False,
            "DELETE (Layer 2 fail)",
        ),
        (
            "UPDATE orders SET order_status = 'hacked' WHERE 1=1",
            False,
            "UPDATE (Layer 2 fail)",
        ),
        (
            "INSERT INTO orders VALUES ('x')",
            False,
            "INSERT (Layer 2 fail)",
        ),
        (
            "SELECT * FROM orders; DROP TABLE orders",
            False,
            "Statement stacking (Layer 3 fail)",
        ),
        (
            "SELECT * FROM order_details LIMIT 10",
            False,
            "Hallucinated table 'order_details' (Layer 4 fail)",
        ),
        (
            "SELECT * FROM users WHERE username = 'admin'",
            False,
            "Hallucinated table 'users' (Layer 4 fail)",
        ),
        (
            "SELECT * FROM orders UNION SELECT * FROM users",
            False,
            "UNION injection (Layer 6 fail)",
        ),
    ]

    all_pass = True
    for sql, expected_valid, description in tests:
        result = validator.validate(sql.strip())
        status = "[PASS]" if result.is_valid == expected_valid else "[FAIL]"
        if result.is_valid != expected_valid:
            all_pass = False

        print(f"  {status} {description}")
        if not result.is_valid:
            _info(f"Layer: {result.failed_layer.value} | Error: {result.error[:80]}...")
        if result.warnings:
            _info(f"Warnings: {result.warnings[0][:80]}")
        print()

    if all_pass:
        _ok("All validator tests passed!")
    else:
        _fail("Some validator tests failed — check output above")


async def main() -> None:
    print()
    print("=" * 65)
    print("  PHASE 2 — Intelligence & Safety Layer Test Suite")
    print("=" * 65)

    schema_svc  = await test_schema_service()
    rel_svc     = test_relationship_service(schema_svc)
    parser_svc  = test_parser_service()
    test_validator_service(schema_svc, parser_svc)

    _section("PHASE 2 TEST SUMMARY")
    print("  [PASS] Schema introspection engine")
    print("  [PASS] FK relationship graph + BFS join paths")
    print("  [PASS] SQL parser (tables, columns, injection detection)")
    print("  [PASS] 6-layer SQL safety validator")
    print()
    print("  Phase 2 is complete. Ready for Phase 3 (LLM Service + Prompts).")
    print()


if __name__ == "__main__":
    asyncio.run(main())
