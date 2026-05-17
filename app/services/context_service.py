"""
app/services/context_service.py
=================================
Intelligent schema context builder -- selects RELEVANT tables for the
LLM prompt rather than blindly dumping the full schema.

WHY intelligent context selection matters
------------------------------------------
The Olist schema has 9 tables. For a question like "which state has the
most customers?", injecting ALL 9 tables wastes:
  - ~800 tokens on irrelevant tables (order_reviews, order_payments, etc.)
  - Model attention on noise that increases hallucination risk

Enterprise systems like Databricks SQL AI, Snowflake Cortex, and Google
Looker AI ALL implement relevance-based context selection.

Our approach: KEYWORD → TABLE MAPPING
---------------------------------------
We maintain a mapping from business vocabulary to table names.
When the user asks "top sellers by revenue", we detect:
  - "seller*"  -> sellers
  - "revenue"  -> order_items (contains price/freight)
  - implicit:  -> orders (needed to filter by status)

Then we expand via the FK graph to include any tables needed to JOIN
the selected tables together.

This is a simplified version of what production systems do with
embeddings-based retrieval (RAG over schema metadata). Our keyword
approach is:
  - Deterministic (no hallucination in context selection)
  - Zero latency (no extra API call)
  - Interpretable (you can see exactly why each table was included)

Token budget:
  - Full schema (9 tables): ~850 tokens
  - Relevant subset (3-4 tables): ~350 tokens
  - Savings: ~500 tokens per query (significant at scale)
"""

import re
from dataclasses import dataclass

from app.core.config import settings
from app.core.logger import logger
from app.schemas.query import SchemaMeta, TableMeta
from app.services.relationship_service import RelationshipService


# ── Business Vocabulary → Table Relevance Map ─────────────────────────────────
# Maps keyword patterns (regex) to relevant table names.
# Order matters: more specific patterns should come first.

_KEYWORD_TABLE_MAP: list[tuple[re.Pattern, list[str]]] = [
    # Revenue / financial
    (re.compile(r'\brevenu|price|payment|freight|value\b', re.I),
     ["order_items", "order_payments", "orders"]),

    # Sellers
    (re.compile(r'\bseller', re.I),
     ["sellers", "order_items", "orders"]),

    # Customers / buyers
    (re.compile(r'\bcustomer|buyer|purchas', re.I),
     ["customers", "orders"]),

    # Products / categories
    (re.compile(r'\bproduct|categor|item\b', re.I),
     ["products", "order_items", "product_category_name_translation"]),

    # Reviews / satisfaction / score / rating
    (re.compile(r'\breview|rating|score|satisfaction|comment', re.I),
     ["order_reviews", "orders"]),

    # Geography / location / state / city
    (re.compile(r'\bstate|city|location|region|geo|zip\b', re.I),
     ["customers", "sellers", "geolocation"]),

    # Delivery / shipping / logistics / delay
    (re.compile(r'\bdeliver|ship|freight|logistic|delay|late|transit', re.I),
     ["orders", "order_items", "sellers"]),

    # Time / monthly / trend / year / date
    (re.compile(r'\bmonth|week|year|trend|time|date|period\b', re.I),
     ["orders", "order_items"]),

    # Orders (generic)
    (re.compile(r'\border\b', re.I),
     ["orders", "order_items"]),

    # Status
    (re.compile(r'\bstatus|deliver|cancel|approv\b', re.I),
     ["orders"]),
]

# Tables that are almost always needed as JOIN bridges
_BRIDGE_TABLES = {"orders", "order_items"}


@dataclass
class ContextPackage:
    """Everything the prompt builder needs from the context service."""
    relevant_tables: dict[str, TableMeta]
    schema_prompt: str          # formatted schema string (token-optimised)
    relationship_prompt: str    # join hints string
    selected_table_names: list[str]
    total_tables_available: int
    context_strategy: str       # "full" | "selective" -- for logging/debugging


class ContextService:
    """
    Builds a token-efficient, query-relevant schema context package for
    injection into the LLM prompt.
    """

    def __init__(
        self,
        schema_meta: SchemaMeta,
        relationship_service: RelationshipService,
        max_tables: int | None = None,
    ) -> None:
        self._schema  = schema_meta
        self._rel_svc = relationship_service
        self._max_tables = max_tables or settings.max_context_tables

    def build_context(self, question: str) -> ContextPackage:
        """
        Build a context package tailored to the user's question.

        Pipeline:
          1. Detect relevant tables from question keywords
          2. Expand to include FK bridge tables needed for JOINs
          3. Cap at max_context_tables for token efficiency
          4. Format as prompt-ready strings
        """
        relevant_table_names = self._detect_relevant_tables(question)
        strategy = "selective"

        # Fallback: if no tables detected, use full schema
        if not relevant_table_names:
            relevant_table_names = set(self._schema.table_names)
            strategy = "full"

        # Always include the core bridge tables (orders, order_items)
        # because almost every analytical query needs them
        relevant_table_names |= _BRIDGE_TABLES

        # Expand: for each relevant table, include its direct FK neighbours
        # so the model can see the JOIN columns
        expanded = set(relevant_table_names)
        for table in relevant_table_names:
            neighbours = self._rel_svc.get_related_tables(table)
            for n in neighbours:
                # Only add neighbours that are themselves relevant
                if n in relevant_table_names:
                    expanded.add(n)

        # Cap to budget
        final_tables = list(expanded)[: self._max_tables]

        # Build relevant TableMeta subset
        relevant_meta = {
            t: self._schema.tables[t]
            for t in final_tables
            if t in self._schema.tables
        }

        # Build filtered relationship list (only for selected tables)
        relevant_rels = [
            rel for rel in self._schema.relationships
            if rel["from_table"] in final_tables
            and rel["to_table"] in final_tables
        ]

        schema_prompt    = self._format_schema(relevant_meta)
        rel_prompt       = self._format_relationships(relevant_rels)

        logger.info(
            "ContextService: built context | strategy={s} | tables={t} | "
            "schema_chars={sc} | rel_chars={rc}",
            s=strategy,
            t=final_tables,
            sc=len(schema_prompt),
            rc=len(rel_prompt),
        )

        return ContextPackage(
            relevant_tables=relevant_meta,
            schema_prompt=schema_prompt,
            relationship_prompt=rel_prompt,
            selected_table_names=final_tables,
            total_tables_available=len(self._schema.tables),
            context_strategy=strategy,
        )

    def _detect_relevant_tables(self, question: str) -> set[str]:
        """Match question tokens against the keyword→table map."""
        detected: set[str] = set()
        for pattern, tables in _KEYWORD_TABLE_MAP:
            if pattern.search(question):
                detected.update(tables)
        return detected

    @staticmethod
    def _format_schema(tables: dict[str, TableMeta]) -> str:
        """
        Format selected tables as a compact, LLM-optimised schema string.

        Design choices:
          - Column names left-padded for readability (model sees alignment)
          - Type + constraint flags on one line (dense = fewer tokens)
          - FK references inline so the model sees join conditions immediately
        """
        lines = ["DATABASE SCHEMA (SQLite)", "=" * 60, ""]
        for table_name, table_meta in tables.items():
            lines.append(f"TABLE: {table_name}")
            lines.append("-" * 40)
            for col in table_meta.columns:
                parts = [f"  {col.name:<40} {col.type}"]
                flags: list[str] = []
                if col.primary_key:
                    flags.append("PK")
                if col.foreign_key:
                    flags.append(f"FK -> {col.foreign_key}")
                if not col.nullable and not col.primary_key:
                    flags.append("NOT NULL")
                if flags:
                    parts.append(f"  [{', '.join(flags)}]")
                lines.append("".join(parts))
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_relationships(rels: list[dict[str, str]]) -> str:
        """Format join conditions as explicit JOIN hints."""
        if not rels:
            return ""
        lines = ["FOREIGN KEY RELATIONSHIPS (use ONLY these for JOINs):", "=" * 60]
        seen: set[tuple] = set()
        for rel in rels:
            key = (rel["from_table"], rel["to_table"])
            rev = (rel["to_table"], rel["from_table"])
            if key in seen or rev in seen:
                continue
            seen.add(key)
            lines.append(
                f"  {rel['from_table']}.{rel['from_column']}"
                f"  =  {rel['to_table']}.{rel['to_column']}"
            )
        return "\n".join(lines)
