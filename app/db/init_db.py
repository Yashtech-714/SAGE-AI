"""
app/db/init_db.py
==================
Database initialisation: creates all tables and loads CSV data.

Strategy & Architecture Decisions
-------------------------------------------------------------------------------
1.  ORDER OF LOADING MATTERS (foreign key dependency chain):
        geolocation  -> customers, sellers
        product_category_name_translation  -> products
        products, sellers  -> order_items
        orders  -> order_items, order_payments, order_reviews

    We load tables in topological order so FK constraints are never violated.

2.  FK CONSTRAINTS DURING LOAD:
    The raw dataset is not perfectly normalized - some customer/seller zip codes
    don't appear in the geolocation table.  We temporarily disable FK checking
    during the bulk load (safe because we control the data source), then
    re-enable it for runtime query safety.

3.  GEOLOCATION DEDUPLICATION:
    The raw CSV has ~1M rows because multiple entries share the same zip prefix
    (different lat/lng samples).  We keep one representative row per prefix
    (the mean lat/lng) so our PK constraint on geolocation_zip_code_prefix holds.

4.  CHUNKED LOADING WITH PANDAS:
    Large CSVs (geolocation ~60 MB, orders ~17 MB) are read in chunks of 10,000
    rows and inserted via bulk inserts.  This avoids loading the whole file into RAM.

5.  IDEMPOTENCY:
    The script checks if data already exists before loading.  Running it twice
    is safe - it will skip tables that already have data.

6.  PRAGMA OPTIMISATIONS (SQLite):
    - PRAGMA journal_mode=WAL  -> write-ahead logging for concurrent reads
    - PRAGMA synchronous=NORMAL -> faster writes, still crash-safe
    - PRAGMA foreign_keys=ON   -> enforce FK constraints (after load)
    - PRAGMA cache_size=-64000 -> 64 MB page cache for analytics queries

7.  WHY NOT ALEMBIC?
    For a single-developer project starting from scratch, SQLAlchemy's
    create_all() is simpler and sufficient.  Alembic can be added later
    if schema migrations become necessary.
-------------------------------------------------------------------------------
"""

import asyncio
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import settings
from app.core.logger import logger
from app.db.session import engine
from app.models.olist import Base  # registers all ORM models

CHUNK_SIZE = 10_000   # rows per pandas chunk

# ── CSV filename -> SQLAlchemy table name (topological FK order) ──────────────
CSV_TO_TABLE: dict[str, str] = {
    "olist_geolocation_dataset.csv":             "geolocation",
    "olist_customers_dataset.csv":               "customers",
    "olist_sellers_dataset.csv":                 "sellers",
    "product_category_name_translation.csv":     "product_category_name_translation",
    "olist_products_dataset.csv":                "products",
    "olist_orders_dataset.csv":                  "orders",
    "olist_order_items_dataset.csv":             "order_items",
    "olist_order_payments_dataset.csv":          "order_payments",
    "olist_order_reviews_dataset.csv":           "order_reviews",
}


# ── Special pre-processing per table ─────────────────────────────────────────

def _process_geolocation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deduplicate geolocation rows by zip prefix.
    The raw CSV has ~1M rows (many samples per zip). We aggregate to one row
    per prefix (mean lat/lng, first city/state) giving ~19K unique prefixes.
    """
    df["geolocation_zip_code_prefix"] = (
        df["geolocation_zip_code_prefix"].astype(str).str.zfill(5)
    )
    return df.groupby("geolocation_zip_code_prefix", as_index=False).agg(
        geolocation_lat=("geolocation_lat", "mean"),
        geolocation_lng=("geolocation_lng", "mean"),
        geolocation_city=("geolocation_city", "first"),
        geolocation_state=("geolocation_state", "first"),
    )


def _process_customers(df: pd.DataFrame) -> pd.DataFrame:
    df["customer_zip_code_prefix"] = (
        df["customer_zip_code_prefix"].astype(str).str.zfill(5)
    )
    return df


def _process_sellers(df: pd.DataFrame) -> pd.DataFrame:
    df["seller_zip_code_prefix"] = (
        df["seller_zip_code_prefix"].astype(str).str.zfill(5)
    )
    return df


def _process_products(df: pd.DataFrame) -> pd.DataFrame:
    # Fill missing category names with a placeholder
    df["product_category_name"] = df["product_category_name"].fillna("unknown")
    return df


def _process_order_reviews(df: pd.DataFrame) -> pd.DataFrame:
    # review_id is not always unique in the raw CSV — deduplicate
    return df.drop_duplicates(subset=["review_id"])


PREPROCESSORS = {
    "geolocation":                   _process_geolocation,
    "customers":                     _process_customers,
    "sellers":                       _process_sellers,
    "products":                      _process_products,
    "order_reviews":                 _process_order_reviews,
}


# ─────────────────────────────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print(msg: str) -> None:
    """Simple stdout print with flush — avoids Rich unicode issues on Windows."""
    print(msg, flush=True)


async def _table_has_data(conn: AsyncConnection, table: str) -> bool:
    """Return True if a table already contains at least one row."""
    result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
    count = result.scalar()
    return (count or 0) > 0


def _build_insert(table: str, columns: list[str]) -> str:
    """
    Build a parameterised INSERT OR IGNORE statement.
    OR IGNORE silently skips rows that violate PK/UNIQUE constraints,
    making the load idempotent for duplicate rows in raw CSVs.
    """
    cols = ", ".join(columns)
    vals = ", ".join(f":{c}" for c in columns)
    return f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({vals})"


async def _load_csv_to_table(
    conn: AsyncConnection,
    csv_path: Path,
    table_name: str,
) -> int:
    """
    Read a CSV in chunks and bulk-insert into the target table.
    Returns total rows inserted.
    """
    total_rows = 0

    if table_name == "geolocation":
        # Must read all at once to deduplicate across the full file
        df_full = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False)
        preprocessor = PREPROCESSORS.get(table_name)
        if preprocessor:
            df_full = preprocessor(df_full)
        df_full = df_full.where(pd.notnull(df_full), None)
        rows = df_full.to_dict(orient="records")
        if rows:
            await conn.execute(
                text(_build_insert(table_name, list(df_full.columns))),
                rows,
            )
        total_rows = len(rows)
    else:
        reader = pd.read_csv(
            csv_path,
            encoding="utf-8-sig",
            low_memory=False,
            chunksize=CHUNK_SIZE,
        )
        preprocessor = PREPROCESSORS.get(table_name)

        chunk_num = 0
        for chunk in reader:
            if preprocessor:
                chunk = preprocessor(chunk)
            chunk = chunk.where(pd.notnull(chunk), None)
            rows = chunk.to_dict(orient="records")
            if rows:
                await conn.execute(
                    text(_build_insert(table_name, list(chunk.columns))),
                    rows,
                )
            total_rows += len(rows)
            chunk_num += 1
            if chunk_num % 5 == 0:
                _print(f"    ... {total_rows:,} rows loaded so far")

    return total_rows


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def init_database() -> None:
    """
    Full database initialisation:
      1. Create all tables (DDL)
      2. Apply SQLite performance PRAGMAs
      3. Disable FK checks, load all CSVs, re-enable FK checks
    """
    dataset_dir = Path(settings.dataset_dir)

    _print("=" * 60)
    _print("  Text-to-SQL -- Database Initialisation")
    _print("=" * 60)
    _print(f"  Database : {settings.db_path}")
    _print(f"  Dataset  : {dataset_dir.resolve()}")
    _print("")

    # Ensure datasets/ directory exists for the .db file
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()

    async with engine.begin() as conn:

        # ── 1. Performance PRAGMAs (excluding FK enforcement for now) ─────
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA synchronous=NORMAL;"))
        await conn.execute(text("PRAGMA cache_size=-64000;"))
        await conn.execute(text("PRAGMA temp_store=MEMORY;"))
        logger.debug("SQLite PRAGMAs applied")

        # ── 2. Create tables (DDL) ────────────────────────────────────────
        _print("[Step 1/3] Creating database schema...")
        await conn.run_sync(Base.metadata.create_all)
        _print("[OK] Schema created\n")

        # ── 3. Disable FK checks during bulk load ─────────────────────────
        #
        # WHY: The Olist dataset is not perfectly normalized. Some customer
        # and seller zip codes don't appear in the geolocation table.
        # Disabling FK checks during load is standard ETL practice.
        # FK enforcement is enabled for all runtime queries via session.py.
        #
        await conn.execute(text("PRAGMA foreign_keys=OFF;"))
        logger.info("FK constraints disabled for bulk load")

        # ── 4. Load CSVs ──────────────────────────────────────────────────
        _print("[Step 2/3] Loading CSV data...")
        _print("")

        total_tables = len(CSV_TO_TABLE)
        loaded_tables = 0

        for csv_filename, table_name in CSV_TO_TABLE.items():
            csv_path = dataset_dir / csv_filename

            if not csv_path.exists():
                _print(f"  [WARN] CSV not found, skipping: {csv_path}")
                logger.warning("CSV not found: {p}", p=csv_path)
                continue

            already_loaded = await _table_has_data(conn, table_name)
            if already_loaded:
                _print(f"  [SKIP] {table_name:<45} already has data")
                logger.info("Skipping already-loaded table: {t}", t=table_name)
                loaded_tables += 1
                continue

            _print(f"  Loading {table_name}...")
            t0 = time.perf_counter()

            rows_inserted = await _load_csv_to_table(conn, csv_path, table_name)
            elapsed = time.perf_counter() - t0
            loaded_tables += 1

            _print(
                f"  [OK]   {table_name:<45} "
                f"{rows_inserted:>9,} rows  ({elapsed:.1f}s)  "
                f"[{loaded_tables}/{total_tables}]"
            )
            logger.info(
                "Loaded table={t} rows={r} time={e:.1f}s",
                t=table_name, r=rows_inserted, e=elapsed,
            )

        # ── 5. Re-enable FK constraints ───────────────────────────────────
        await conn.execute(text("PRAGMA foreign_keys=ON;"))
        logger.info("FK constraints re-enabled")

    total_elapsed = time.perf_counter() - start
    _print("")
    _print("[Step 3/3] Post-load PRAGMA FK enforcement re-enabled")
    _print("")
    _print("=" * 60)
    _print(f"  [DONE] Initialisation complete in {total_elapsed:.1f}s")
    _print("=" * 60)
    logger.info("Database initialisation complete in {e:.1f}s", e=total_elapsed)


# ── CLI entry point ────────────────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(init_database())
