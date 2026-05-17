"""
app/schemas/query.py
=====================
Pydantic data models — typed contracts for every stage in the pipeline.

Architecture Decision: Typed Inter-Layer Contracts
---------------------------------------------------
In production AI systems, passing raw dicts between pipeline stages
is a reliability anti-pattern. Pydantic models provide:

  1. Runtime validation  -- bad data caught at layer boundaries, not deep inside
  2. Auto-generated OpenAPI -- FastAPI reads these for /docs
  3. Serialisation -- .model_dump() gives JSON-safe dicts for API responses
  4. Self-documentation -- Field(description=...) annotates every field

Pipeline flow:
  NLQueryRequest
      |
      v (ContextService)
  ContextPackage (in context_service.py -- dataclass, not Pydantic)
      |
      v (PromptService -> LLMService)
  LLMResponse (in llm_service.py -- dataclass)
      |
      v (ValidatorService)
  ValidationResult
      |
      v (ExecutionService)
  QueryExecutionResult
      |
      v (ExplanationAgent)
  QueryResponse  <-- what the API returns
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class QueryStatus(str, Enum):
    SUCCESS          = "success"
    VALIDATION_FAILED= "validation_failed"
    EXECUTION_FAILED = "execution_failed"
    RETRY_EXHAUSTED  = "retry_exhausted"
    FORBIDDEN        = "forbidden"


class ValidationLayer(str, Enum):
    SYNTAX             = "syntax"
    FORBIDDEN_KEYWORD  = "forbidden_keyword"
    MULTIPLE_STATEMENTS= "multiple_statements"
    TABLE_NOT_FOUND    = "table_not_found"
    COLUMN_NOT_FOUND   = "column_not_found"
    INJECTION_PATTERN  = "injection_pattern"
    PASSED             = "passed"


# ─────────────────────────────────────────────────────────────────────────────
# Request
# ─────────────────────────────────────────────────────────────────────────────

class NLQueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Natural language business question",
        examples=["Which sellers generated the highest revenue?"],
    )
    max_rows: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum rows to return",
    )
    include_insight: bool = Field(
        default=True,
        description="Whether to generate a business insight explanation",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"question": "Which sellers generated the highest revenue?", "max_rows": 10}]
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Schema Metadata (SchemaService output)
# ─────────────────────────────────────────────────────────────────────────────

class ColumnMeta(BaseModel):
    name: str
    type: str
    nullable: bool
    primary_key: bool
    foreign_key: str | None = Field(None, description="'table.column' if FK, else None")


class TableMeta(BaseModel):
    table_name: str
    columns: list[ColumnMeta]
    primary_keys: list[str]
    foreign_keys: list[dict[str, str]]
    row_count: int | None = None


class SchemaMeta(BaseModel):
    tables: dict[str, TableMeta]
    relationships: list[dict[str, str]]
    table_names: list[str]
    total_tables: int


# ─────────────────────────────────────────────────────────────────────────────
# SQL Validation (ValidatorService output)
# ─────────────────────────────────────────────────────────────────────────────

class ValidationResult(BaseModel):
    is_valid: bool
    sql: str
    failed_layer: ValidationLayer = ValidationLayer.PASSED
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# SQL Generation (LLM output)
# ─────────────────────────────────────────────────────────────────────────────

class SQLGenerationResult(BaseModel):
    sql: str
    model_used: str
    attempt_number: int = 1
    prompt_tokens: int = 0
    completion_tokens: int = 0

    model_config = {"protected_namespaces": ()}


# ─────────────────────────────────────────────────────────────────────────────
# Query Execution (ExecutionService output)
# ─────────────────────────────────────────────────────────────────────────────

class QueryExecutionResult(BaseModel):
    """Output of the SQL execution step."""
    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    was_capped: bool = False        # True if result was truncated to max_rows
    error: str | None = None        # None means success

    @property
    def success(self) -> bool:
        return self.error is None


# ─────────────────────────────────────────────────────────────────────────────
# Final API Response (what the client receives)
# ─────────────────────────────────────────────────────────────────────────────

class QueryResponse(BaseModel):
    """
    Complete response returned to the API consumer.

    Designed for transparency: includes the generated SQL, retry count,
    and token usage -- essential for debugging AI pipelines and for
    demonstrating the system's intelligence to stakeholders.
    """
    question: str
    sql: str                                        # The final executed SQL
    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0

    # Business insight (from ExplanationAgent)
    insight: str | None = None

    # Pipeline observability
    execution_time_ms: float = 0.0
    total_time_ms: float = 0.0
    attempts: int = 1                               # How many LLM attempts were needed
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_used: str = ""
    context_tables: list[str] = Field(default_factory=list)  # Which tables were in context

    # Result metadata
    was_capped: bool = False
    success: bool = True
    error: str | None = None

    model_config = {"protected_namespaces": ()}


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    database: str = "connected"
    llm_provider: str = ""
    llm_model: str = ""
    tables_loaded: int = 0
    api_key_configured: bool = False
