"""
scripts/verify_db.py
=====================
Post-initialisation verification script.
Prints row counts and sample data for every table so you can confirm
the database loaded correctly before moving to Phase 2.

Run:
    python scripts/verify_db.py
"""

import asyncio

from rich.console import Console
from rich.table import Table
from sqlalchemy import text

from app.db.session import engine

console = Console()

TABLES = [
    "geolocation",
    "customers",
    "sellers",
    "product_category_name_translation",
    "products",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
]


async def verify() -> None:
    console.rule("[bold blue]Database Verification")

    table = Table(
        "Table", "Row Count", "Sample PK / First Column",
        title="Olist SQLite Database",
        show_lines=True,
    )

    async with engine.connect() as conn:
        for t in TABLES:
            count_result = await conn.execute(text(f"SELECT COUNT(*) FROM {t}"))
            count = count_result.scalar()

            # Grab first row for a sanity check
            sample_result = await conn.execute(text(f"SELECT * FROM {t} LIMIT 1"))
            row = sample_result.fetchone()
            sample = str(row[0]) if row else "—"

            table.add_row(t, f"{count:,}", sample)

    console.print(table)
    console.print()

    # Bonus: a quick join query to validate FK integrity
    console.rule("[bold yellow]FK Integrity Spot-Check")
    async with engine.connect() as conn:
        sql = """
            SELECT
                COUNT(DISTINCT o.order_id)   AS total_orders,
                COUNT(DISTINCT oi.order_id)  AS orders_with_items,
                COUNT(DISTINCT op.order_id)  AS orders_with_payments,
                COUNT(DISTINCT c.customer_id) AS total_customers
            FROM orders o
            LEFT JOIN order_items oi   ON o.order_id = oi.order_id
            LEFT JOIN order_payments op ON o.order_id = op.order_id
            LEFT JOIN customers c      ON o.customer_id = c.customer_id
        """
        result = await conn.execute(text(sql))
        row = result.fetchone()
        console.print(f"  Total orders        : [cyan]{row[0]:,}[/cyan]")
        console.print(f"  Orders with items   : [cyan]{row[1]:,}[/cyan]")
        console.print(f"  Orders with payments: [cyan]{row[2]:,}[/cyan]")
        console.print(f"  Total customers     : [cyan]{row[3]:,}[/cyan]")
        console.print()

    console.print("[bold green][OK] Verification complete[/bold green]")


if __name__ == "__main__":
    asyncio.run(verify())
