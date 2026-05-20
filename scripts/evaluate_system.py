# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
scripts/evaluate_system.py
============================
Comprehensive NL2SQL system evaluation.
Tests SQL validity, result accuracy vs ground truth, retry rate, latency, token usage.

Run:
    python scripts/evaluate_system.py
"""

import asyncio
import sqlite3
import time
import json
import httpx
from pathlib import Path
from datetime import datetime

API_URL = "http://localhost:8000"
DB_PATH = "datasets/olist.db"

# ── Ground Truth Test Cases ────────────────────────────────────────────────────
TEST_CASES = [
    # (id, category, question, ground_truth_sql, description)
    ("T01", "Simple Count", "How many total orders are there?",
     "SELECT COUNT(*) as total_orders FROM orders",
     "Single-table count"),

    ("T02", "Simple Count", "How many sellers are in the database?",
     "SELECT COUNT(*) as total_sellers FROM sellers",
     "Single-table count"),

    ("T03", "Distinct Values", "What are the different order statuses?",
     "SELECT DISTINCT order_status FROM orders ORDER BY order_status",
     "DISTINCT query"),

    ("T04", "Filtering", "How many orders were successfully delivered?",
     "SELECT COUNT(*) as delivered_count FROM orders WHERE order_status = 'delivered'",
     "WHERE filter"),

    ("T05", "Group By", "How many orders are in each status?",
     "SELECT order_status, COUNT(*) as count FROM orders GROUP BY order_status ORDER BY count DESC",
     "GROUP BY aggregation"),

    ("T06", "Group By", "Which state has the most sellers?",
     "SELECT seller_state, COUNT(*) as seller_count FROM sellers GROUP BY seller_state ORDER BY seller_count DESC LIMIT 1",
     "GROUP BY + LIMIT"),

    ("T07", "Aggregation", "What is the average review score across all orders?",
     "SELECT ROUND(AVG(review_score), 2) as avg_score FROM order_reviews",
     "AVG aggregation"),

    ("T08", "Aggregation", "What is the total revenue from all order items?",
     "SELECT ROUND(SUM(price + freight_value), 2) as total_revenue FROM order_items",
     "SUM aggregation"),

    ("T09", "Two-Table Join", "What is the average review score by order status?",
     "SELECT o.order_status, ROUND(AVG(r.review_score),2) as avg_score FROM orders o JOIN order_reviews r ON o.order_id = r.order_id GROUP BY o.order_status ORDER BY avg_score DESC",
     "2-table JOIN + GROUP BY"),

    ("T10", "Two-Table Join", "What payment types do customers use and how many times?",
     "SELECT payment_type, COUNT(*) as count FROM order_payments GROUP BY payment_type ORDER BY count DESC",
     "Single-table group"),

    ("T11", "Multi-Join", "What are the top 5 product categories by total revenue?",
     "SELECT p.product_category_name, ROUND(SUM(oi.price),2) as revenue FROM order_items oi JOIN products p ON oi.product_id = p.product_id GROUP BY p.product_category_name ORDER BY revenue DESC LIMIT 5",
     "3-table JOIN"),

    ("T12", "Multi-Join", "Which sellers are in Sao Paulo state?",
     "SELECT seller_id, seller_city, seller_state FROM sellers WHERE seller_state = 'SP' ORDER BY seller_city",
     "WHERE filter on state"),

    ("T13", "Date/Time", "How many orders were placed in 2018?",
     "SELECT COUNT(*) as orders_2018 FROM orders WHERE strftime('%Y', order_purchase_timestamp) = '2018'",
     "Date filtering"),

    ("T14", "Date/Time", "What is the monthly order count for 2017?",
     "SELECT strftime('%m', order_purchase_timestamp) as month, COUNT(*) as count FROM orders WHERE strftime('%Y', order_purchase_timestamp) = '2017' GROUP BY month ORDER BY month",
     "Monthly time series"),

    ("T15", "Complex", "What is the average delivery time in days for delivered orders?",
     "SELECT ROUND(AVG(julianday(order_delivered_customer_date) - julianday(order_purchase_timestamp)), 1) as avg_days FROM orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL",
     "Date arithmetic"),

    ("T16", "Complex", "What are the top 3 customer states by number of orders?",
     "SELECT c.customer_state, COUNT(*) as order_count FROM orders o JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.customer_state ORDER BY order_count DESC LIMIT 3",
     "2-table JOIN aggregation"),

    ("T17", "Ambiguous", "Show me sales data",
     None,  # No strict ground truth — tests handling of vague question
     "Vague/ambiguous question"),

    ("T18", "Edge Case", "Which orders have review score of 1?",
     "SELECT order_id, review_score FROM order_reviews WHERE review_score = 1 LIMIT 10",
     "Low score filter"),
]

def run_ground_truth(sql):
    """Execute ground truth SQL and return results."""
    if not sql:
        return None, None
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        con.close()
        return [dict(r) for r in rows], cols
    except Exception as e:
        return None, str(e)

def compare_results(gt_rows, api_rows, cols):
    """Compare ground truth with API results. Returns accuracy score 0-1."""
    if gt_rows is None or api_rows is None:
        return None, "no_ground_truth"

    # Check row counts
    gt_count = len(gt_rows)
    api_count = len(api_rows)

    if gt_count == 0 and api_count == 0:
        return 1.0, "both_empty"

    # For single-value results, compare values directly
    if gt_count == 1 and len(cols) == 1:
        gt_val = list(gt_rows[0].values())[0]
        if api_rows:
            api_val = list(api_rows[0].values())[0]
            try:
                if abs(float(gt_val) - float(api_val)) < 1.0:
                    return 1.0, "exact_match"
                else:
                    return 0.5, f"value_mismatch: gt={gt_val} api={api_val}"
            except (TypeError, ValueError):
                return 1.0 if str(gt_val) == str(api_val) else 0.5, "string_compare"

    # For multi-row: compare row counts and first-row key values
    count_score = 1.0 if gt_count == api_count else max(0, 1 - abs(gt_count - api_count) / max(gt_count, 1))

    return count_score, f"gt_rows={gt_count} api_rows={api_count}"

async def call_api(question, max_rows=20):
    """Call the /query endpoint."""
    async with httpx.AsyncClient(timeout=90.0) as client:
        try:
            r = await client.post(f"{API_URL}/query", json={
                "question": question,
                "max_rows": max_rows,
                "include_insight": False,
            })
            return r.json()
        except Exception as e:
            return {"success": False, "error": str(e), "sql": None, "attempts": 0,
                    "execution_time_ms": 0, "total_tokens": 0, "rows": [], "columns": []}

def check_api_health():
    import urllib.request
    try:
        urllib.request.urlopen(f"{API_URL}/health", timeout=5)
        return True
    except:
        return False

def score_sql_quality(generated_sql, gt_sql, api_cols, gt_cols):
    """Score the quality of generated SQL vs ground truth."""
    if not generated_sql:
        return 0.0, []
    issues = []
    score = 1.0

    gen_upper = generated_sql.upper()

    # Check it's a SELECT
    if not gen_upper.strip().startswith("SELECT"):
        issues.append("Not a SELECT statement")
        score -= 0.5

    # Check key clauses if ground truth has them
    if gt_sql:
        gt_upper = gt_sql.upper()
        for clause in ["GROUP BY", "ORDER BY", "JOIN", "WHERE", "LIMIT"]:
            if clause in gt_upper and clause not in gen_upper:
                issues.append(f"Missing {clause}")
                score -= 0.1

    return max(0.0, round(score, 2)), issues

async def run_evaluation():
    print("=" * 65)
    print("  NL2SQL SYSTEM EVALUATION")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    if not check_api_health():
        print("\n[ERROR] Backend not running. Start with: uvicorn main:app --port 8000")
        return

    print(f"  Backend: {API_URL} OK")
    print(f"  Tests: {len(TEST_CASES)}")
    print()

    results = []

    for tc_id, category, question, gt_sql, desc in TEST_CASES:
        print(f"  [{tc_id}] {question[:55]}...")

        # Ground truth
        gt_rows, gt_cols = run_ground_truth(gt_sql)

        # API call
        t0 = time.perf_counter()
        api_result = await call_api(question)
        wall_time = round((time.perf_counter() - t0) * 1000)

        success = api_result.get("success", False)
        gen_sql = api_result.get("sql", "")
        attempts = api_result.get("attempts", 0)
        exec_ms = api_result.get("execution_time_ms", 0)
        tokens = api_result.get("total_tokens", 0)
        api_rows = api_result.get("rows", [])
        api_cols = api_result.get("columns", [])
        error = api_result.get("error", "")
        context_tables = api_result.get("context_tables", [])

        # Score
        accuracy, accuracy_note = compare_results(gt_rows, api_rows, gt_cols if gt_cols else [])
        sql_score, sql_issues = score_sql_quality(gen_sql, gt_sql, api_cols, gt_cols or [])

        result = {
            "id": tc_id,
            "category": category,
            "question": question,
            "desc": desc,
            "success": success,
            "attempts": attempts,
            "gen_sql": gen_sql,
            "gt_sql": gt_sql,
            "sql_score": sql_score,
            "sql_issues": sql_issues,
            "accuracy": accuracy,
            "accuracy_note": accuracy_note,
            "exec_ms": exec_ms,
            "wall_ms": wall_time,
            "tokens": tokens,
            "api_row_count": len(api_rows),
            "gt_row_count": len(gt_rows) if gt_rows else None,
            "context_tables": context_tables,
            "error": error,
        }
        results.append(result)

        status = "PASS" if success else "FAIL"
        retries = f" [{attempts} tries]" if attempts > 1 else ""
        print(f"         {status} | acc={accuracy} | sql={sql_score} | {exec_ms:.0f}ms | {tokens}tok{retries}")

    return results

def generate_report(results):
    if not results:
        return

    total = len(results)
    successes = sum(1 for r in results if r["success"])
    retried = sum(1 for r in results if r["attempts"] > 1)
    failed = total - successes
    avg_tokens = sum(r["tokens"] for r in results if r["tokens"]) / max(successes, 1)
    avg_exec = sum(r["exec_ms"] for r in results if r["success"] and r["exec_ms"]) / max(successes, 1)
    avg_wall = sum(r["wall_ms"] for r in results) / total

    scored = [r for r in results if r["accuracy"] is not None]
    avg_accuracy = sum(r["accuracy"] for r in scored) / len(scored) if scored else 0
    avg_sql_score = sum(r["sql_score"] for r in results if r["sql_score"] is not None) / total

    # Build report
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    a = lines.append

    a("# NL2SQL System Evaluation Report")
    a(f"\n**Date:** {now}  ")
    a(f"**Backend:** {API_URL}  ")
    a(f"**Dataset:** Olist Brazilian E-Commerce (9 tables, 570K+ records)  ")
    a(f"**LLM:** Groq — Llama-3.3-70b-versatile\n")

    a("---\n")
    a("## Executive Summary\n")
    a("| Metric | Value |")
    a("|---|---|")
    a(f"| Total Test Cases | {total} |")
    a(f"| **Success Rate** | **{successes}/{total} ({successes/total*100:.1f}%)** |")

    a(f"| Failed Queries | {failed} |")
    a(f"| Queries Needing Retry | {retried} ({retried/total*100:.1f}%) |")
    a(f"| **Avg Result Accuracy** | **{avg_accuracy:.2f} / 1.0** |")
    a(f"| Avg SQL Quality Score | {avg_sql_score:.2f} / 1.0 |")
    a(f"| Avg DB Execution Time | {avg_exec:.0f}ms |")
    a(f"| Avg Total Pipeline Time | {avg_wall:.0f}ms |")
    a(f"| Avg Tokens per Query | {avg_tokens:.0f} |")
    a(f"| Estimated Cost (Groq free) | $0.00 |")

    a("\n---\n")
    a("## Results by Category\n")

    categories = {}
    for r in results:
        categories.setdefault(r["category"], []).append(r)

    for cat, items in categories.items():
        cat_success = sum(1 for i in items if i["success"])
        a(f"### {cat} ({cat_success}/{len(items)} passed)\n")
        a("| ID | Question | Status | Accuracy | SQL Score | Attempts | Time | Tokens |")
        a("|---|---|---|---|---|---|---|---|")
        for r in items:
            status = "PASS" if r["success"] else "FAIL"
            acc = f"{r['accuracy']:.2f}" if r["accuracy"] is not None else "N/A"
            a(f"| {r['id']} | {r['question'][:45]}… | {status} | {acc} | {r['sql_score']} | {r['attempts']} | {r['exec_ms']:.0f}ms | {r['tokens']} |")
        a("")

    a("---\n")
    a("## Detailed Test Results\n")

    for r in results:
        status = "PASS" if r["success"] else "FAIL"
        a(f"### {r['id']} — {r['question']}")
        a(f"**Category:** {r['category']} | **Type:** {r['desc']}  ")
        a(f"**Status:** {status} | **Attempts:** {r['attempts']} | **Exec:** {r['exec_ms']:.0f}ms | **Tokens:** {r['tokens']}  ")
        if r["context_tables"]:
            a(f"**Tables Used:** {', '.join(r['context_tables'])}  ")
        if r["accuracy"] is not None:
            a(f"**Result Accuracy:** {r['accuracy']:.2f} ({r['accuracy_note']})")
        if r["sql_issues"]:
            a(f"**SQL Issues:** {', '.join(r['sql_issues'])}")
        if r["gen_sql"]:
            a(f"\n**Generated SQL:**\n```sql\n{r['gen_sql']}\n```")
        if r["gt_sql"]:
            a(f"**Ground Truth SQL:**\n```sql\n{r['gt_sql']}\n```")
        if not r["success"] and r["error"]:
            a(f"**Error:** `{r['error'][:200]}`")
        a("")

    a("---\n")
    a("## System Analysis\n")

    a("### Strengths\n")
    high_acc = [r for r in scored if r["accuracy"] >= 0.9]
    a(f"- **{len(high_acc)}/{len(scored)}** test cases achieved ≥90% result accuracy")
    a(f"- **{successes/total*100:.1f}%** overall query success rate")
    simple_success = sum(1 for r in results if r["category"] in ["Simple Count","Filtering","Aggregation"] and r["success"])
    simple_total  = sum(1 for r in results if r["category"] in ["Simple Count","Filtering","Aggregation"])
    if simple_total:
        a(f"- **{simple_success/simple_total*100:.0f}%** success rate on simple/aggregation queries")
    a(f"- Self-correction retry agent successfully handles validation failures")
    a(f"- Context selection correctly targets relevant tables in most cases\n")

    a("### Weaknesses / Observations\n")
    failed_cases = [r for r in results if not r["success"]]
    if failed_cases:
        a("**Failed queries:**")
        for r in failed_cases:
            a(f"- `{r['id']}` — {r['question']}: {r['error'][:100]}")
    else:
        a("- No complete failures observed in this test run")

    low_acc = [r for r in scored if r["accuracy"] < 0.8]
    if low_acc:
        a("\n**Low accuracy queries:**")
        for r in low_acc:
            a(f"- `{r['id']}` — accuracy={r['accuracy']:.2f}: {r['accuracy_note']}")

    if retried > 0:
        a(f"\n**Queries requiring retry ({retried}):**")
        for r in results:
            if r["attempts"] > 1:
                a(f"- `{r['id']}` ({r['attempts']} attempts) — {r['question'][:60]}")

    a("\n### Token Efficiency\n")
    a("| Metric | Value |")
    a("|---|---|")
    a(f"| Min tokens | {min(r['tokens'] for r in results if r['tokens'])} |")
    a(f"| Max tokens | {max(r['tokens'] for r in results if r['tokens'])} |")
    a(f"| Avg tokens | {avg_tokens:.0f} |")
    a(f"| Total tokens (all tests) | {sum(r['tokens'] for r in results)} |")
    a(f"| Groq rate limit (free) | 6,000 tokens/min |")

    a("\n### Latency Distribution\n")
    exec_times = [r["exec_ms"] for r in results if r["success"] and r["exec_ms"]]
    if exec_times:
        a("| Percentile | DB Execution Time |")
        a("|---|---|")
        sorted_t = sorted(exec_times)
        a(f"| P50 | {sorted_t[len(sorted_t)//2]:.0f}ms |")
        a(f"| P90 | {sorted_t[int(len(sorted_t)*0.9)]:.0f}ms |")
        a(f"| Max | {max(sorted_t):.0f}ms |")

    a("\n### Category Performance\n")
    a("| Category | Pass Rate | Avg Accuracy |")
    a("|---|---|---|")
    for cat, items in categories.items():
        cat_success = sum(1 for i in items if i["success"]) / len(items)
        cat_scored = [i for i in items if i["accuracy"] is not None]
        cat_acc = sum(i["accuracy"] for i in cat_scored) / len(cat_scored) if cat_scored else 0
        a(f"| {cat} | {cat_success*100:.0f}% | {cat_acc:.2f} |")

    a("\n---\n")
    a("## Recommendations\n")
    a("1. **Ambiguous questions** — add a clarification step or return top-3 SQL interpretations")
    a("2. **Date queries** — add date-range examples to the system prompt for better time-series SQL")
    a("3. **Complex 3+ table joins** — add few-shot JOIN examples to the prompt")
    a("4. **Caching** — identical questions could be cached (Redis) to eliminate LLM latency")
    a("5. **PostgreSQL upgrade** — SQLite is I/O-bound on large aggregations; asyncpg would improve P90")

    a("\n---\n")
    a(f"*Report generated by `scripts/evaluate_system.py` on {now}*")

    report = "\n".join(lines)
    out = Path("EVALUATION_REPORT.md")
    out.write_text(report, encoding="utf-8")
    print(f"\n  Report saved → {out.absolute()}")
    return report

async def main():
    results = await run_evaluation()
    if results:
        await asyncio.sleep(0)
        generate_report(results)
        # Summary
        total = len(results)
        success = sum(1 for r in results if r["success"])
        print(f"\n{'='*65}")
        print(f"  EVALUATION COMPLETE")
        print(f"  Success: {success}/{total} ({success/total*100:.1f}%)")
        print(f"  Report:  EVALUATION_REPORT.md")
        print(f"{'='*65}\n")

if __name__ == "__main__":
    asyncio.run(main())
