"""
app/api/routes/schema.py
===========================
GET /schema — expose database schema metadata for debugging and demos.

WHY expose the schema via API?
----------------------------------
1. Debugging: when the LLM generates wrong table names, you can check
   the schema endpoint to verify what tables actually exist.

2. Client intelligence: a frontend can read /schema to build a
   "helpful hints" panel showing available tables and columns.

3. Demo value: investors and interviewers can see the full relational
   model without needing DB access.

4. Integration: downstream services can introspect the schema without
   direct DB access (API-first architecture principle).

Response design:
  Returns the full SchemaMeta (tables, columns, FK relationships).
  In production, you'd add auth (API key / JWT) to protect this endpoint.
"""

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_schema_meta, get_schema_service
from app.schemas.query import SchemaMeta
from app.services.schema_service import SchemaService

router = APIRouter(tags=["Schema"])


@router.get(
    "/schema",
    summary="Database schema metadata",
    description="Returns all tables, columns, foreign keys, and relationships in the Olist database.",
)
async def get_schema(
    schema_meta: SchemaMeta = Depends(get_schema_meta),
) -> dict:
    """Return the full database schema in a structured, human-readable format."""

    tables_summary = []
    for table_name, table_meta in schema_meta.tables.items():
        tables_summary.append({
            "table": table_name,
            "columns": [
                {
                    "name": col.name,
                    "type": col.type,
                    "primary_key": col.primary_key,
                    "foreign_key": col.foreign_key,
                    "nullable": col.nullable,
                }
                for col in table_meta.columns
            ],
            "primary_keys": table_meta.primary_keys,
            "foreign_keys": table_meta.foreign_keys,
        })

    return {
        "total_tables": schema_meta.total_tables,
        "tables": tables_summary,
        "relationships": schema_meta.relationships,
        "relationship_count": len(schema_meta.relationships),
    }


@router.get(
    "/schema/tables",
    summary="List available table names",
    description="Returns just the list of table names — useful for quick validation.",
)
async def list_tables(
    schema_meta: SchemaMeta = Depends(get_schema_meta),
) -> dict:
    return {
        "tables": schema_meta.table_names,
        "total": schema_meta.total_tables,
    }


@router.get(
    "/schema/table/{table_name}",
    summary="Get schema for a specific table",
)
async def get_table_schema(
    table_name: str,
    schema_meta: SchemaMeta = Depends(get_schema_meta),
) -> dict:
    """Return column metadata for a specific table."""
    if table_name not in schema_meta.tables:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"Table '{table_name}' not found. Available: {schema_meta.table_names}",
        )

    table_meta = schema_meta.tables[table_name]
    return {
        "table": table_name,
        "columns": [col.model_dump() for col in table_meta.columns],
        "primary_keys": table_meta.primary_keys,
        "foreign_keys": table_meta.foreign_keys,
    }
