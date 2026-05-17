"""
app/services/schema_service.py
================================
Schema Introspection Engine — reads the live SQLite database schema and
produces structured metadata for two consumers:

  1.  The LLM prompt engine   — needs schema as a formatted string
  2.  The SQL validator        — needs column/table sets for existence checks

Architecture Decisions
----------------------
WHY introspect the live DB rather than hard-coding the schema?

  In production Text-to-SQL systems, the database schema evolves.
  Hard-coding schema means the system breaks silently when tables change.
  Introspecting the live DB means the validator is ALWAYS accurate — it
  validates against reality, not a stale copy.

HOW it works:

  SQLAlchemy's `inspect()` function reads the DB's internal catalog
  (sqlite_master for SQLite, information_schema for PostgreSQL) and returns
  structured metadata.  We convert this into three artifacts:

    a) SchemaMeta Pydantic model    — for validation and API responses
    b) Prompt-ready string          — formatted for LLM context window injection
    c) Column/table sets            — fast O(1) lookups during validation

WHY cache the schema?

  Schema introspection involves DB I/O.  The schema doesn't change at runtime,
  so we cache it in-process.  If the schema changes, restart the app — this
  is standard FastAPI startup behavior.

Enterprise context:
  In production systems like Databricks SQL AI or Snowflake Cortex, the schema
  layer is often a separate microservice backed by a metadata catalog (e.g.
  Apache Atlas, DataHub).  For our system, the SQLAlchemy inspector IS that
  catalog.
"""

from functools import lru_cache
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logger import logger
from app.schemas.query import ColumnMeta, SchemaMeta, TableMeta

# ── Human-readable type mapping ───────────────────────────────────────────────
# SQLAlchemy returns internal type objects; we convert to clean strings
# so the LLM reads "TEXT" not "VARCHAR(50)"
_TYPE_MAP: dict[str, str] = {
    "varchar": "TEXT",
    "text":    "TEXT",
    "integer": "INTEGER",
    "float":   "FLOAT",
    "numeric": "FLOAT",
    "boolean": "BOOLEAN",
    "date":    "DATE",
    "datetime":"DATETIME",
}


def _normalise_type(raw_type: str) -> str:
    """Convert SQLAlchemy type string to a clean, LLM-readable type name."""
    lower = raw_type.lower()
    for key, clean in _TYPE_MAP.items():
        if key in lower:
            return clean
    return raw_type.upper()


# ─────────────────────────────────────────────────────────────────────────────
# SchemaService
# ─────────────────────────────────────────────────────────────────────────────

class SchemaService:
    """
    Introspects the live database and exposes schema metadata in multiple formats.

    Usage:
        schema_svc = SchemaService(engine)
        await schema_svc.load()

        # For the validator:
        valid_tables = schema_svc.get_valid_tables()
        valid_cols   = schema_svc.get_valid_columns("orders")

        # For the LLM prompt:
        prompt_block = schema_svc.get_prompt_context()
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._schema_meta: SchemaMeta | None = None
        # Fast-lookup sets (populated after load)
        self._table_names: set[str] = set()
        self._columns_by_table: dict[str, set[str]] = {}

    # ── Public: initialise (call once at app startup) ─────────────────────────

    async def load(self) -> SchemaMeta:
        """
        Introspect the database and build all metadata structures.
        This is idempotent — calling it twice is safe (returns cached result).
        """
        if self._schema_meta is not None:
            return self._schema_meta

        logger.info("SchemaService: starting schema introspection")

        tables: dict[str, TableMeta] = {}
        relationships: list[dict[str, str]] = []

        # SQLAlchemy inspection runs synchronously; we delegate to a thread
        async with self._engine.connect() as conn:
            table_names: list[str] = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )

            for table_name in table_names:
                table_meta = await self._introspect_table(conn, table_name)
                tables[table_name] = table_meta

                # Collect FK relationships for the global relationship list
                for fk in table_meta.foreign_keys:
                    relationships.append({
                        "from_table":      table_name,
                        "from_column":     fk["column"],
                        "to_table":        fk["references_table"],
                        "to_column":       fk["references_column"],
                        "join_hint":       (
                            f"{table_name}.{fk['column']} = "
                            f"{fk['references_table']}.{fk['references_column']}"
                        ),
                    })

        self._schema_meta = SchemaMeta(
            tables=tables,
            relationships=relationships,
            table_names=list(tables.keys()),
            total_tables=len(tables),
        )

        # Build fast-lookup structures
        self._table_names = set(tables.keys())
        for tname, tmeta in tables.items():
            self._columns_by_table[tname] = {col.name for col in tmeta.columns}

        logger.info(
            "SchemaService: loaded {n} tables, {r} FK relationships",
            n=len(tables),
            r=len(relationships),
        )
        return self._schema_meta

    # ── Internal: per-table introspection ────────────────────────────────────

    async def _introspect_table(self, conn: Any, table_name: str) -> TableMeta:
        """Introspect a single table and return its TableMeta."""

        def _sync_introspect(sync_conn: Any) -> tuple:
            insp = inspect(sync_conn)
            columns_raw = insp.get_columns(table_name)
            pk_info      = insp.get_pk_constraint(table_name)
            fk_info      = insp.get_foreign_keys(table_name)
            return columns_raw, pk_info, fk_info

        columns_raw, pk_info, fk_info = await conn.run_sync(_sync_introspect)

        pk_cols: list[str] = pk_info.get("constrained_columns", [])

        # Build ColumnMeta list
        columns: list[ColumnMeta] = []
        for col in columns_raw:
            # Determine FK reference for this column
            fk_ref = None
            for fk in fk_info:
                if col["name"] in fk.get("constrained_columns", []):
                    ref_table = fk.get("referred_table", "")
                    ref_cols  = fk.get("referred_columns", [""])
                    fk_ref    = f"{ref_table}.{ref_cols[0]}"

            columns.append(ColumnMeta(
                name=col["name"],
                type=_normalise_type(str(col["type"])),
                nullable=col.get("nullable", True),
                primary_key=col["name"] in pk_cols,
                foreign_key=fk_ref,
            ))

        # Build structured FK list
        foreign_keys: list[dict[str, str]] = []
        for fk in fk_info:
            for constrained_col, referred_col in zip(
                fk.get("constrained_columns", []),
                fk.get("referred_columns", []),
            ):
                foreign_keys.append({
                    "column":            constrained_col,
                    "references_table":  fk.get("referred_table", ""),
                    "references_column": referred_col,
                })

        return TableMeta(
            table_name=table_name,
            columns=columns,
            primary_keys=pk_cols,
            foreign_keys=foreign_keys,
        )

    # ── Public: validator helpers ─────────────────────────────────────────────

    def get_valid_tables(self) -> set[str]:
        """Return the set of all valid table names. O(1) lookup."""
        return self._table_names

    def get_valid_columns(self, table_name: str) -> set[str]:
        """Return the set of valid column names for a table. O(1) lookup."""
        return self._columns_by_table.get(table_name, set())

    def is_valid_table(self, table_name: str) -> bool:
        return table_name.lower() in {t.lower() for t in self._table_names}

    def is_valid_column(self, table_name: str, column_name: str) -> bool:
        cols = self._columns_by_table.get(table_name, set())
        return column_name.lower() in {c.lower() for c in cols}

    def get_schema_meta(self) -> SchemaMeta:
        if self._schema_meta is None:
            raise RuntimeError("SchemaService.load() has not been called yet")
        return self._schema_meta

    # ── Public: LLM prompt context ────────────────────────────────────────────

    def get_prompt_context(self) -> str:
        """
        Build a compact, LLM-optimised schema string for prompt injection.

        Format designed to be:
          - Dense (minimises token count)
          - Unambiguous (explicit PKs, FKs, types)
          - Relationship-explicit (JOIN hints inline)

        This string will be embedded in every SQL generation prompt.
        Token efficiency is critical in production to control API costs.
        """
        if self._schema_meta is None:
            raise RuntimeError("SchemaService.load() has not been called yet")

        lines: list[str] = [
            "DATABASE SCHEMA (SQLite)",
            "=" * 60,
            "",
        ]

        for table_name, table_meta in self._schema_meta.tables.items():
            lines.append(f"TABLE: {table_name}")
            lines.append("-" * 40)
            for col in table_meta.columns:
                parts = [f"  {col.name:<45} {col.type}"]
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

        lines.append("FOREIGN KEY RELATIONSHIPS (use for JOINs)")
        lines.append("=" * 60)
        for rel in self._schema_meta.relationships:
            lines.append(
                f"  {rel['from_table']}.{rel['from_column']}"
                f"  ->  {rel['to_table']}.{rel['to_column']}"
            )

        return "\n".join(lines)

    def get_table_summary(self) -> str:
        """
        One-line summary per table for quick orientation.
        Used in shorter prompts where full schema would waste tokens.
        """
        if self._schema_meta is None:
            raise RuntimeError("SchemaService.load() has not been called yet")

        lines = ["AVAILABLE TABLES:"]
        for tname, tmeta in self._schema_meta.tables.items():
            col_names = [c.name for c in tmeta.columns]
            lines.append(f"  {tname}: {', '.join(col_names)}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton factory
# ─────────────────────────────────────────────────────────────────────────────

_schema_service_instance: SchemaService | None = None


async def get_schema_service(engine: AsyncEngine) -> SchemaService:
    """
    Return the shared SchemaService instance, initialising it if needed.
    FastAPI's dependency injection will call this once at startup.
    """
    global _schema_service_instance
    if _schema_service_instance is None:
        _schema_service_instance = SchemaService(engine)
        await _schema_service_instance.load()
    return _schema_service_instance
