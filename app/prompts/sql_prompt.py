"""
app/prompts/sql_prompt.py
===========================
SQL generation prompt builder and few-shot examples.

Few-Shot Prompting Strategy
-----------------------------
Few-shot examples are the single most effective technique for improving
SQL generation accuracy in production systems.

WHY few-shot over zero-shot?
  - LLMs have seen millions of SQL examples in training, but they don't know
    YOUR schema, YOUR column names, or YOUR business definitions.
  - Few-shot examples teach the model the exact patterns it needs:
    which tables to join, how to write aggregations, alias conventions.
  - 3-5 well-chosen examples reduce hallucination rates by ~40-60% compared
    to zero-shot (based on Snowflake + Databricks SQL AI research).

HOW we select examples:
  - Cover the most common SQL patterns: aggregation, multi-table join,
    time-series grouping, filtering, ranking, nested queries.
  - Each example is a real question a business user would ask.
  - Examples use the ACTUAL Olist schema to prevent confusion.

Token efficiency:
  - We include 3 examples in the standard prompt (not all 6) to stay within
    the context budget for gpt-4o-mini / grok-3-mini (128k context, but
    shorter prompts are cheaper and faster).
  - The prompt_service selects the most relevant examples based on the
    query type detected in the user's question.
"""

from dataclasses import dataclass


@dataclass
class FewShotExample:
    question: str
    sql: str
    pattern: str   # tag for retrieval: aggregation | join | timeseries | filter | ranking


# ── Production Few-Shot Examples ─────────────────────────────────────────────
# All validated against the live Olist SQLite database.

FEW_SHOT_EXAMPLES: list[FewShotExample] = [

    FewShotExample(
        pattern="ranking",
        question="Which sellers generated the highest total revenue?",
        sql="""SELECT
    s.seller_id,
    s.seller_city,
    s.seller_state,
    COUNT(DISTINCT o.order_id)                        AS total_orders,
    ROUND(SUM(oi.price + oi.freight_value), 2)        AS total_revenue
FROM sellers s
JOIN order_items oi ON s.seller_id = oi.seller_id
JOIN orders o       ON oi.order_id = o.order_id
WHERE o.order_status = 'delivered'
GROUP BY s.seller_id, s.seller_city, s.seller_state
ORDER BY total_revenue DESC
LIMIT 10""",
    ),

    FewShotExample(
        pattern="timeseries",
        question="Show monthly revenue trends over all years.",
        sql="""SELECT
    strftime('%Y-%m', o.order_purchase_timestamp)     AS month,
    COUNT(DISTINCT o.order_id)                        AS total_orders,
    ROUND(SUM(oi.price + oi.freight_value), 2)        AS total_revenue
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.order_status = 'delivered'   -- completed orders only
GROUP BY month
ORDER BY month
LIMIT 50""",
    ),

    FewShotExample(
        pattern="aggregation",
        question="What is the average review score by product category?",
        sql="""SELECT
    t.product_category_name_english                   AS category,
    ROUND(AVG(r.review_score), 2)                     AS avg_review_score,
    COUNT(r.review_id)                                AS total_reviews
FROM order_reviews r
JOIN orders o       ON r.order_id = o.order_id
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p     ON oi.product_id = p.product_id
JOIN product_category_name_translation t
    ON p.product_category_name = t.product_category_name
WHERE o.order_status = 'delivered'
  AND t.product_category_name_english IS NOT NULL
GROUP BY t.product_category_name_english
ORDER BY avg_review_score DESC
LIMIT 20""",
    ),

    FewShotExample(
        pattern="filter",
        question="Which states have the most customers?",
        sql="""SELECT
    customer_state                                    AS state,
    COUNT(DISTINCT customer_unique_id)                AS unique_customers
FROM customers
GROUP BY customer_state
ORDER BY unique_customers DESC
LIMIT 15""",
    ),

    FewShotExample(
        pattern="join",
        question="What are the products with the highest average freight cost?",
        sql="""SELECT
    p.product_id,
    t.product_category_name_english                   AS category,
    ROUND(AVG(oi.freight_value), 2)                   AS avg_freight,
    COUNT(oi.order_item_id)                           AS times_ordered
FROM products p
JOIN order_items oi ON p.product_id = oi.product_id
LEFT JOIN product_category_name_translation t
    ON p.product_category_name = t.product_category_name
GROUP BY p.product_id, t.product_category_name_english
HAVING times_ordered >= 5
ORDER BY avg_freight DESC
LIMIT 15""",
    ),

    FewShotExample(
        pattern="timeseries",
        question="How many days on average does delivery take by seller state?",
        sql="""SELECT
    s.seller_state,
    ROUND(AVG(
        julianday(o.order_delivered_customer_date)
        - julianday(o.order_purchase_timestamp)
    ), 1)                                             AS avg_delivery_days,
    COUNT(DISTINCT o.order_id)                        AS total_orders
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN sellers s      ON oi.seller_id = s.seller_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
GROUP BY s.seller_state
ORDER BY avg_delivery_days ASC
LIMIT 30""",
    ),
]


def format_few_shot_block(examples: list[FewShotExample]) -> str:
    """
    Render few-shot examples as a formatted prompt block.

    Format:
        EXAMPLE 1:
        Question: ...
        SQL:
        SELECT ...

    This explicit structure is important: it teaches the model the exact
    input -> output mapping pattern, and it makes it unambiguous where
    the SQL starts and ends (no markdown fences needed in examples because
    we're already in a structured block).
    """
    lines: list[str] = ["EXAMPLES OF CORRECT SQL:", "=" * 60, ""]
    for i, ex in enumerate(examples, 1):
        lines.append(f"EXAMPLE {i}:")
        lines.append(f"Question: {ex.question}")
        lines.append("SQL:")
        lines.append(ex.sql)
        lines.append("")
    return "\n".join(lines)


def build_user_prompt(
    question: str,
    schema_context: str,
    relationship_context: str,
    few_shot_block: str,
    max_rows: int = 50,
) -> str:
    """
    Assemble the complete user-turn prompt.

    Prompt structure (ordered by importance to the model):
      1. Schema context  -- what tables/columns exist
      2. Relationships   -- how to JOIN them
      3. Few-shot examples -- pattern library
      4. The actual question + row limit

    WHY this ordering?
      Models attend more strongly to information close to the instruction.
      The question + row limit are last so they're in the most attended
      position (recency bias in transformer attention).
    """
    return f"""{schema_context}

{relationship_context}

{few_shot_block}
{"=" * 60}
NOW ANSWER THIS QUESTION:
Question: {question}
Row limit: {max_rows}

SQL:"""
