"""
app/prompts/retry_prompt.py
=============================
Prompt templates for the SQL correction/retry agent.

WHY a dedicated retry prompt?
--------------------------------
When SQL generation fails (validation error or DB execution error), we don't
re-run the same prompt -- that would produce the same wrong SQL.

We build a CORRECTION prompt that gives the LLM:
  1. The original question (intent)
  2. The WRONG SQL it generated (so it can see its own mistake)
  3. The EXACT ERROR MESSAGE (so it knows what to fix)
  4. The schema (refresher -- maybe it hallucinated a column name)
  5. Explicit instruction: "fix the SQL, output ONLY SQL"

This is the key insight from enterprise AI systems:
  - Snowflake Cortex, Databricks DBRX, and Google Duet SQL all implement
    self-correction loops.
  - The error message IS the training signal -- no fine-tuning needed.
  - LLMs are excellent at reading error messages and fixing code.

Retry agent behaviour in production:
  - Attempt 1: standard SQL generation
  - Attempt 2: correction prompt with validator error
  - Attempt 3: correction prompt with DB execution error (if attempt 2 ran)
  - After max_retries: return structured failure with all attempt details
"""


RETRY_SYSTEM_PROMPT = """You are an expert SQL debugger for a Brazilian e-commerce analytics platform.

A previous SQL query failed. Your job is to fix it.

STRICT RULES:
- Respond with ONLY the corrected SQL query -- no explanation, no markdown
- Use ONLY tables and columns that exist in the provided schema
- Fix the EXACT error shown -- do not rewrite unrelated parts of the query
- Output a single, executable SQLite SELECT statement
- Always include LIMIT"""


def build_retry_prompt(
    question: str,
    failed_sql: str,
    error_message: str,
    schema_context: str,
    attempt_number: int,
) -> str:
    """
    Build the correction prompt for a failed SQL attempt.

    The error message is the most critical element -- it tells the LLM
    exactly what went wrong. We structure it clearly so the model can
    pattern-match the error type:

    - "Tables not found: ['order_details']" -> table hallucination
    - "no such column: total_revenue"       -> column hallucination
    - "syntax error near..."                -> syntax mistake

    Each error type has a predictable correction pattern the LLM
    has seen in training data (Stack Overflow, GitHub issues, etc.).
    """
    return f"""You generated SQL for this question but it failed. Fix it.

ORIGINAL QUESTION: {question}

ATTEMPT {attempt_number} — FAILED SQL:
{failed_sql}

ERROR:
{error_message}

{schema_context}

Fix the SQL. Output ONLY the corrected SELECT statement:"""


def build_explanation_prompt(
    question: str,
    sql: str,
    rows: list[dict],
    columns: list[str],
    row_count: int,
) -> str:
    """
    Prompt for the business insight explanation layer.

    We pass the actual query results (truncated to first 10 rows for token
    efficiency) so the LLM can reference specific numbers in its explanation.

    WHY include actual data in the prompt?
      Without data, the model can only describe what the SQL does.
      With data, it can say "Electronics generated R$2.1M, 27% of total revenue"
      -- specific, actionable, interview-worthy.
    """
    # Truncate to first 10 rows for token efficiency
    sample_rows = rows[:10]

    rows_text = ""
    if sample_rows:
        # Format as a simple table
        header = " | ".join(columns)
        rows_text = header + "\n"
        rows_text += "-" * len(header) + "\n"
        for row in sample_rows:
            rows_text += " | ".join(str(row.get(col, "")) for col in columns) + "\n"
        if row_count > 10:
            rows_text += f"... ({row_count - 10} more rows)"

    return f"""You are a business analytics expert. Summarize these query results in 2-3 concise sentences.

QUESTION ASKED: {question}

SQL EXECUTED:
{sql}

RESULTS ({row_count} total rows):
{rows_text}

Write a business insight summary that:
- States the key finding with specific numbers from the data
- Mentions trends or patterns if visible
- Uses business language, not technical SQL language
- Is 2-3 sentences maximum

Business Insight:"""
