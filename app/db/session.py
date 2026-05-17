"""
app/db/session.py
==================
SQLAlchemy engine + session factory.

Why async engine?
  - FastAPI is an async framework; using a sync engine would block the event loop
    on every DB call, defeating the purpose of async I/O.
  - aiosqlite provides an async SQLite driver compatible with SQLAlchemy's
    async extension.

Connection pool notes:
  - SQLite is file-based, so pool_pre_ping and check_same_thread=False are
    mandatory for safe multi-threaded / async use.
  - In production with PostgreSQL, replace the URL and remove check_same_thread.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logger import logger

# ── Convert a sync sqlite:/// URL to async sqlite+aiosqlite:/// ──────────────
def _make_async_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url


ASYNC_DATABASE_URL = _make_async_url(settings.database_url)

# ── Engine ────────────────────────────────────────────────────────────────────
engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=not settings.is_production,    # log SQL in dev, silent in prod
    connect_args={"check_same_thread": False} if "sqlite" in ASYNC_DATABASE_URL else {},
    pool_pre_ping=True,                 # validate connection before use
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,             # keep objects usable after commit
    autoflush=False,
    autocommit=False,
)


# ── Dependency helper (used by FastAPI routes) ────────────────────────────────
async def get_db() -> AsyncSession:
    """
    FastAPI dependency that yields a DB session and closes it after the request.
    Usage:
        @router.get("/")
        async def handler(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


logger.debug("Database engine created | url={url}", url=ASYNC_DATABASE_URL)
