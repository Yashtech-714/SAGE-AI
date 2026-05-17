"""
app/services/relationship_service.py
======================================
Table Relationship Mapper — builds and queries a FK graph of the database.

Architecture Decision — WHY a dedicated relationship service?
--------------------------------------------------------------
In relational Text-to-SQL, the hardest problem is not generating SQL syntax
— it is understanding WHICH tables to join and HOW.

Without relationship reasoning:
  "Which sellers have the highest revenue?"
  → LLM may try to join orders directly to sellers, missing order_items

With relationship reasoning:
  → The system knows the JOIN path:
    sellers  ←→  order_items  ←→  orders

This service builds an undirected graph of the FK relationships and exposes:

  1. find_join_path()    — shortest path between two tables (BFS)
  2. get_join_sql()      — renders that path as a SQL JOIN chain
  3. get_related_tables() — direct neighbors of a table
  4. format_for_prompt() — human-readable join hints for LLM context

WHY BFS for join paths?
  BFS finds the shortest path (fewest joins), which produces the most
  efficient SQL.  DFS might return a longer, less efficient path.

Enterprise context:
  Enterprise systems like Snowflake's metadata service use graph traversal
  to suggest JOIN paths at query planning time.  Our implementation is a
  simplified version of the same concept.

Olist FK Graph:
  customers ─── orders ─── order_items ─── products ─── product_category_name_translation
                   │              │
            order_payments   order_items ─── sellers ─── geolocation
            order_reviews

All edges are undirected for path-finding (JOIN works both directions).
"""

from collections import deque

from app.core.logger import logger
from app.schemas.query import SchemaMeta


class RelationshipService:
    """
    Builds a bidirectional adjacency graph from FK metadata and exposes
    join-path reasoning for the LLM prompt engine.

    Graph representation:
        { table_name: [ { neighbor, from_col, to_col } ] }

    We use an adjacency list (dict of lists) because:
      - Sparse graph (O(tables) nodes, O(FKs) edges)
      - Fast neighbor lookup for BFS
      - Easy to serialise for prompt injection
    """

    def __init__(self, schema_meta: SchemaMeta) -> None:
        self._schema = schema_meta
        # Adjacency list: table -> list of edge dicts
        self._graph: dict[str, list[dict[str, str]]] = {
            t: [] for t in schema_meta.table_names
        }
        self._build_graph()

    # ── Graph Construction ────────────────────────────────────────────────────

    def _build_graph(self) -> None:
        """
        Build a BIDIRECTIONAL graph from FK relationships.

        We add edges in both directions because SQL JOINs are symmetric:
          orders.customer_id → customers.customer_id
        can be written as:
          FROM orders JOIN customers ...
        OR:
          FROM customers JOIN orders ...
        """
        for rel in self._schema.relationships:
            from_t  = rel["from_table"]
            from_c  = rel["from_column"]
            to_t    = rel["to_table"]
            to_c    = rel["to_column"]

            # Forward edge: from_table → to_table
            self._graph.setdefault(from_t, []).append({
                "neighbor":   to_t,
                "local_col":  from_c,
                "remote_col": to_c,
                "direction":  "forward",
            })

            # Backward edge: to_table → from_table (for BFS from any side)
            self._graph.setdefault(to_t, []).append({
                "neighbor":   from_t,
                "local_col":  to_c,
                "remote_col": from_c,
                "direction":  "backward",
            })

        total_edges = sum(len(v) for v in self._graph.values())
        logger.info(
            "RelationshipService: graph built | nodes={n} | edges={e}",
            n=len(self._graph),
            e=total_edges // 2,  # bidirectional so divide by 2
        )

    # ── Path Finding ──────────────────────────────────────────────────────────

    def find_join_path(
        self, source: str, target: str
    ) -> list[dict[str, str]] | None:
        """
        BFS shortest path between two tables.

        Returns a list of edge dicts representing the JOIN chain, or None
        if no path exists.

        Example: find_join_path("sellers", "orders")
          → [
              {from: "sellers", to: "order_items", on: "sellers.seller_id = order_items.seller_id"},
              {from: "order_items", to: "orders",   on: "order_items.order_id = orders.order_id"},
            ]
        """
        if source not in self._graph:
            logger.warning("RelationshipService: unknown source table: {t}", t=source)
            return None
        if target not in self._graph:
            logger.warning("RelationshipService: unknown target table: {t}", t=target)
            return None
        if source == target:
            return []

        # BFS
        visited: set[str] = {source}
        # Queue entries: (current_table, path_so_far)
        queue: deque[tuple[str, list[dict[str, str]]]] = deque([(source, [])])

        while queue:
            current, path = queue.popleft()
            for edge in self._graph.get(current, []):
                neighbor = edge["neighbor"]
                join_step = {
                    "from":       current,
                    "to":         neighbor,
                    "local_col":  edge["local_col"],
                    "remote_col": edge["remote_col"],
                    "on":         f"{current}.{edge['local_col']} = {neighbor}.{edge['remote_col']}",
                }
                new_path = path + [join_step]
                if neighbor == target:
                    logger.debug(
                        "RelationshipService: path found {src} -> {tgt} | hops={h}",
                        src=source, tgt=target, h=len(new_path),
                    )
                    return new_path
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, new_path))

        logger.warning(
            "RelationshipService: no path between {s} and {t}", s=source, t=target
        )
        return None

    def get_join_sql(self, source: str, target: str) -> str | None:
        """
        Render the shortest path between two tables as a SQL JOIN clause.

        Example: get_join_sql("sellers", "customers")
          → "JOIN order_items ON sellers.seller_id = order_items.seller_id
             JOIN orders      ON order_items.order_id = orders.order_id
             JOIN customers   ON orders.customer_id = customers.customer_id"
        """
        path = self.find_join_path(source, target)
        if path is None:
            return None
        if not path:
            return ""  # same table

        join_clauses = [
            f"JOIN {step['to']} ON {step['on']}"
            for step in path
        ]
        return "\n".join(join_clauses)

    # ── Neighbor Queries ──────────────────────────────────────────────────────

    def get_related_tables(self, table: str) -> list[str]:
        """Return the direct FK neighbors of a table."""
        return [edge["neighbor"] for edge in self._graph.get(table, [])]

    def get_all_paths_from(self, source: str) -> dict[str, list[dict[str, str]] | None]:
        """
        Run BFS from source to ALL other tables.
        Returns a dict mapping every reachable table to its shortest path.
        Useful for pre-computing the full join map at startup.
        """
        if source not in self._graph:
            return {}
        return {
            target: self.find_join_path(source, target)
            for target in self._graph
            if target != source
        }

    # ── Schema Validation Helpers ─────────────────────────────────────────────

    def tables_are_connected(self, table_a: str, table_b: str) -> bool:
        """Return True if the two tables are reachable from each other."""
        return self.find_join_path(table_a, table_b) is not None

    # ── LLM Prompt Formatting ─────────────────────────────────────────────────

    def format_for_prompt(self) -> str:
        """
        Format the relationship graph as an LLM-readable JOIN reference.

        This block is injected into the system prompt so the LLM understands
        which JOIN conditions to use without hallucinating column names.

        Format:
            TABLE RELATIONSHIPS & JOIN CONDITIONS
            =====================================
            orders → customers
              JOIN: orders.customer_id = customers.customer_id
            order_items → orders
              JOIN: order_items.order_id = orders.order_id
            ...
        """
        lines = [
            "TABLE RELATIONSHIPS & JOIN CONDITIONS",
            "=" * 60,
            "Use ONLY these join conditions. Never invent join columns.",
            "",
        ]

        seen: set[tuple[str, str]] = set()
        for rel in self._schema.relationships:
            pair = (rel["from_table"], rel["to_table"])
            reverse = (rel["to_table"], rel["from_table"])
            if pair in seen or reverse in seen:
                continue
            seen.add(pair)
            lines.append(
                f"  {rel['from_table']}.{rel['from_column']}"
                f"  =  {rel['to_table']}.{rel['to_column']}"
            )

        lines.append("")
        lines.append("EXAMPLE JOIN PATHS:")
        lines.append("-" * 40)

        # Pre-compute a few critical paths for the Olist schema
        critical_paths = [
            ("sellers", "orders"),
            ("customers", "products"),
            ("orders", "products"),
        ]
        for src, tgt in critical_paths:
            if src in self._graph and tgt in self._graph:
                sql = self.get_join_sql(src, tgt)
                if sql:
                    lines.append(f"  -- {src} -> {tgt}")
                    for join_line in sql.split("\n"):
                        lines.append(f"  {join_line}")
                    lines.append("")

        return "\n".join(lines)

    def get_adjacency_summary(self) -> dict[str, list[str]]:
        """
        Return a clean adjacency dict for debugging / API responses.
        { "orders": ["customers", "order_items", "order_payments", "order_reviews"] }
        """
        return {
            table: self.get_related_tables(table)
            for table in self._graph
        }
