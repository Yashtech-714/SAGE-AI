"""
app/services/validator_service.py
===================================
Multi-Layer SQL Safety Validator — the security and correctness gatekeeper.

This is one of the most critical components in the entire system.

WHY is a validator needed in an AI system?
-------------------------------------------
LLMs are probabilistic — they can hallucinate:
  - Table names that don't exist ("order_details" vs "order_items")
  - Column names that don't exist ("total_revenue" vs "price")
  - Dangerous DML that was never intended (UPDATE, DELETE)
  - Multi-statement SQL designed to exfiltrate or corrupt data

Without a validator, ANY hallucination reaches the database engine.
With a validator, hallucinations are caught and fed back to the LLM for
correction BEFORE execution (see Phase 4 retry agent).

WHY layered validation?
------------------------
Defence-in-depth is the industry standard for security systems.

Imagine a castle with:
  Layer 1 — Drawbridge (basic syntax gate)
  Layer 2 — Outer wall (forbidden keywords)
  Layer 3 — Inner wall (multiple statement detection)
  Layer 4 — Guard post (schema: table existence)
  Layer 5 — Treasury vault (schema: column existence)
  Layer 6 — Pattern recognition (injection & obfuscation)

Each layer catches different attack/hallucination vectors.
A query must pass ALL layers to reach the database.

This mirrors the architecture used in enterprise AI SQL systems at
Snowflake (SQL API), Google BigQuery (Data Studio), and Databricks (SQL AI).

Validation Layers
------------------
1. SYNTAX          — Is there any SQL at all?
2. FORBIDDEN_KW    — Does it contain DROP/DELETE/UPDATE/INSERT/ALTER/TRUNCATE?
3. MULTI_STMT      — Does it contain multiple statements? (injection via stacking)
4. TABLE_EXIST     — Do all referenced tables actually exist in the schema?
5. COLUMN_EXIST    — Do referenced columns exist in their respective tables?
6. INJECTION       — Does it match known SQL injection patterns?

The validator short-circuits at the first failure (fail-fast principle).
Detailed error messages are returned to the LLM correction agent.
"""

import time

from app.core.config import settings
from app.core.logger import logger
from app.schemas.query import ValidationLayer, ValidationResult
from app.services.parser_service import ParserService
from app.services.schema_service import SchemaService


class ValidatorService:
    """
    Multi-layer SQL validator.

    Usage:
        validator = ValidatorService(schema_service, parser_service)
        result = validator.validate(sql)

        if result.is_valid:
            # safe to execute
        else:
            # send result.error back to LLM for correction
    """

    def __init__(
        self,
        schema_service: SchemaService,
        parser_service: ParserService,
    ) -> None:
        self._schema  = schema_service
        self._parser  = parser_service

    # ── Main Entry Point ──────────────────────────────────────────────────────

    def validate(self, sql: str) -> ValidationResult:
        """
        Run the full 6-layer validation pipeline.
        Returns a ValidationResult with is_valid=True if all layers pass.

        Execution order is critical:
          - Fast cheap checks first (syntax, keywords)
          - Expensive DB-lookup checks last (schema validation)
        """
        start = time.perf_counter()
        logger.info("ValidatorService: starting validation | sql_len={n}", n=len(sql))

        # ── Layer 1: Syntax Gate ──────────────────────────────────────────
        result = self._check_syntax(sql)
        if not result.is_valid:
            self._log_failure(result, start)
            return result

        # Parse the SQL (we'll reuse the result across all remaining layers)
        parse_result = self._parser.parse(result.sql)

        # ── Layer 2: Forbidden Keyword Detection ──────────────────────────
        result = self._check_forbidden_keywords(result.sql, parse_result)
        if not result.is_valid:
            self._log_failure(result, start)
            return result

        # ── Layer 3: Multiple Statement Detection ─────────────────────────
        result = self._check_single_statement(result.sql, parse_result)
        if not result.is_valid:
            self._log_failure(result, start)
            return result

        # ── Layer 4: Table Existence Validation ───────────────────────────
        result = self._check_tables_exist(result.sql, parse_result)
        if not result.is_valid:
            self._log_failure(result, start)
            return result

        # ── Layer 5: Column Existence Validation ──────────────────────────
        result = self._check_columns_exist(result.sql, parse_result)
        if not result.is_valid:
            self._log_failure(result, start)
            return result

        # ── Layer 6: Injection Pattern Detection ──────────────────────────
        result = self._check_injection_patterns(result.sql, parse_result)
        if not result.is_valid:
            self._log_failure(result, start)
            return result

        # ── All layers passed ─────────────────────────────────────────────
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "ValidatorService: PASSED | tables={t} | time={ms:.1f}ms",
            t=result.referenced_tables,
            ms=elapsed,
        )
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 1 — Syntax Gate
    # ─────────────────────────────────────────────────────────────────────────

    def _check_syntax(self, sql: str) -> ValidationResult:
        """
        Minimal syntax check:
          - Not empty
          - Not just whitespace
          - Starts with SELECT (after stripping)
          - Does not start with a forbidden keyword

        Why check "starts with SELECT" here?
          It's the cheapest possible gate. If the LLM returned an empty
          string or a non-SELECT, there's no point parsing further.
        """
        cleaned = sql.strip().rstrip(";").strip() if sql else ""

        if not cleaned:
            return ValidationResult(
                is_valid=False,
                sql=cleaned,
                failed_layer=ValidationLayer.SYNTAX,
                error="SQL is empty. Please generate a valid SELECT query.",
            )

        first_word = cleaned.split()[0].upper() if cleaned.split() else ""

        if first_word != "SELECT":
            return ValidationResult(
                is_valid=False,
                sql=cleaned,
                failed_layer=ValidationLayer.SYNTAX,
                error=(
                    f"Query must start with SELECT. "
                    f"Got '{first_word}' instead. "
                    "Only read-only SELECT queries are permitted."
                ),
            )

        return ValidationResult(is_valid=True, sql=cleaned)

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 2 — Forbidden Keyword Detection
    # ─────────────────────────────────────────────────────────────────────────

    def _check_forbidden_keywords(self, sql: str, parse_result) -> ValidationResult:
        """
        Scan the token stream for DML/DDL keywords that must never appear.

        We check the PARSED token stream (not raw text) to avoid false positives
        from column values or strings containing these words.

        Example false positive if checking raw text:
          SELECT * FROM orders WHERE status = 'DROPPED'
          → 'DROP' appears in 'DROPPED' but is not a SQL keyword here

        Using sqlparse token types (DML, Keyword) avoids this.
        """
        forbidden_found = parse_result.forbidden_keywords_found

        if forbidden_found:
            keyword_list = ", ".join(forbidden_found)
            logger.warning(
                "ValidatorService: forbidden keywords detected | keywords={k}",
                k=keyword_list,
            )
            return ValidationResult(
                is_valid=False,
                sql=sql,
                failed_layer=ValidationLayer.FORBIDDEN_KEYWORD,
                error=(
                    f"Forbidden SQL keywords detected: [{keyword_list}]. "
                    "Only SELECT queries are allowed. "
                    "Do not generate DDL or DML statements."
                ),
            )

        return ValidationResult(is_valid=True, sql=sql)

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 3 — Multiple Statement Detection
    # ─────────────────────────────────────────────────────────────────────────

    def _check_single_statement(self, sql: str, parse_result) -> ValidationResult:
        """
        Detect statement stacking — a classic SQL injection vector:
          SELECT * FROM orders; DROP TABLE orders; --

        sqlparse counts the number of complete statements.
        We allow exactly one.

        WHY is multi-statement dangerous?
          Even if the first statement is safe SELECT, any subsequent statements
          execute in the same DB connection.  An attacker (or prompt injector)
          could append DROP/DELETE after a valid SELECT.
        """
        if parse_result.statement_count > 1:
            logger.warning(
                "ValidatorService: multiple statements detected | count={n}",
                n=parse_result.statement_count,
            )
            return ValidationResult(
                is_valid=False,
                sql=sql,
                failed_layer=ValidationLayer.MULTIPLE_STATEMENTS,
                error=(
                    f"Multiple SQL statements detected ({parse_result.statement_count}). "
                    "Only a single SELECT statement is allowed per query. "
                    "Remove all statements after the first semicolon."
                ),
            )

        return ValidationResult(is_valid=True, sql=sql)

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 4 — Table Existence Validation
    # ─────────────────────────────────────────────────────────────────────────

    def _check_tables_exist(self, sql: str, parse_result) -> ValidationResult:
        """
        Validate that every table referenced in FROM / JOIN clauses exists
        in the actual database schema.

        This is the PRIMARY anti-hallucination layer.

        Common LLM hallucinations this catches:
          - "order_details" (doesn't exist; real name: "order_items")
          - "product_categories" (doesn't exist; use join to translation table)
          - "users" (doesn't exist in Olist)
          - "revenue_summary" (doesn't exist; must be computed with GROUP BY)

        The error message is structured to be LLM-parseable — it tells the LLM
        exactly which tables are wrong and what the valid options are.
        """
        referenced = parse_result.tables
        valid_tables = self._schema.get_valid_tables()

        # Normalise for case-insensitive comparison
        valid_lower = {t.lower() for t in valid_tables}

        hallucinated: list[str] = []
        for table in referenced:
            if table.lower() not in valid_lower:
                hallucinated.append(table)

        if hallucinated:
            valid_list = ", ".join(sorted(valid_tables))
            logger.warning(
                "ValidatorService: hallucinated tables={h}", h=hallucinated
            )
            return ValidationResult(
                is_valid=False,
                sql=sql,
                failed_layer=ValidationLayer.TABLE_NOT_FOUND,
                error=(
                    f"Tables not found in database: {hallucinated}. "
                    f"Valid tables are: [{valid_list}]. "
                    "Rewrite the query using only these table names."
                ),
                referenced_tables=referenced,
            )

        return ValidationResult(
            is_valid=True,
            sql=sql,
            referenced_tables=referenced,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 5 — Column Existence Validation
    # ─────────────────────────────────────────────────────────────────────────

    def _check_columns_exist(self, sql: str, parse_result) -> ValidationResult:
        """
        Validate that referenced columns exist in their respective tables.

        This layer is BEST EFFORT because:
          - Aliases (SUM(price) AS total_revenue) produce column names that don't exist in the schema
          - Aggregate functions wrap real columns
          - sqlparse column extraction is imperfect for complex queries

        Strategy:
          - Only flag columns that CLEARLY don't match any schema column
          - Skip validation for aggregate functions and expressions
          - Skip '*' (wildcard)
          - Log warnings without failing for ambiguous cases

        WHY not fail hard on column validation?
          Enterprise Text-to-SQL systems use soft validation here because
          computed columns (aliases, functions) are always valid SQL even if
          they don't match a schema column name.  Hard failure would block
          legitimate queries like:
            SELECT SUM(price) AS total_revenue FROM order_items
          where "total_revenue" is not in the schema but is perfectly valid SQL.
        """
        referenced_columns = parse_result.columns
        referenced_tables  = parse_result.tables

        if not referenced_columns or not referenced_tables:
            # Nothing to validate — either SELECT * or complex expression
            return ValidationResult(
                is_valid=True,
                sql=sql,
                referenced_tables=referenced_tables,
                referenced_columns=referenced_columns,
            )

        # Aggregate functions to skip
        agg_functions = {"sum", "count", "avg", "min", "max", "group_concat",
                         "coalesce", "nullif", "ifnull", "strftime", "cast", "round"}

        # Build the union of all valid columns across referenced tables
        all_valid_columns: set[str] = set()
        for table in referenced_tables:
            all_valid_columns.update(
                c.lower() for c in self._schema.get_valid_columns(table)
            )

        warnings: list[str] = []
        for col in referenced_columns:
            col_lower = col.lower()
            if col_lower in ("*", "") or col_lower in agg_functions:
                continue
            if col_lower not in all_valid_columns:
                # Soft warning — column might be an alias or function result
                warning = (
                    f"Column '{col}' not found in tables {referenced_tables}. "
                    "If this is an alias or computed expression, this is OK."
                )
                warnings.append(warning)
                logger.debug("ValidatorService: unrecognised column={c}", c=col)

        return ValidationResult(
            is_valid=True,           # soft validation — don't block
            sql=sql,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            warnings=warnings,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 6 — Injection Pattern Detection
    # ─────────────────────────────────────────────────────────────────────────

    def _check_injection_patterns(self, sql: str, parse_result) -> ValidationResult:
        """
        Check for SQL injection signatures detected by the parser.

        The parser already ran injection detection — we surface the result here.

        WHY check here AND in the parser?
          The parser runs early and stores the result in parse_result.
          We check it here as the LAST validation step so it's part of the
          official layered pipeline and gets logged/rejected at the right level.

        Patterns detected (see parser_service.py for full list):
          - Statement stacking:    ; DROP TABLE ...
          - Comment injection:     -- hidden payload
          - Block comment:         /* payload */
          - Extended procs:        xp_cmdshell (SQL Server)
          - Hex obfuscation:       0x41414141
          - CHAR() obfuscation:    CHAR(65)||CHAR(66)
          - UNION injection:       UNION SELECT NULL, password FROM users
        """
        if parse_result.has_injection_pattern:
            logger.warning(
                "ValidatorService: injection pattern blocked | detail={d}",
                d=parse_result.injection_detail,
            )
            return ValidationResult(
                is_valid=False,
                sql=sql,
                failed_layer=ValidationLayer.INJECTION_PATTERN,
                error=(
                    f"SQL injection pattern detected: {parse_result.injection_detail}. "
                    "This query has been blocked for security reasons."
                ),
            )

        return ValidationResult(
            is_valid=True,
            sql=sql,
            referenced_tables=parse_result.tables,
            referenced_columns=parse_result.columns,
            failed_layer=ValidationLayer.PASSED,
        )

    # ── Internal logging ──────────────────────────────────────────────────────

    @staticmethod
    def _log_failure(result: ValidationResult, start: float) -> None:
        elapsed = (time.perf_counter() - start) * 1000
        logger.warning(
            "ValidatorService: FAILED | layer={layer} | error={err} | time={ms:.1f}ms",
            layer=result.failed_layer,
            err=result.error,
            ms=elapsed,
        )
