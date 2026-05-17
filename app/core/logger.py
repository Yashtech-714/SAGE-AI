"""
app/core/logger.py
==================
Structured logging powered by Loguru.

Why Loguru over stdlib logging?
  - Single-line setup (no Handler/Formatter boilerplate)
  - Automatic exception serialisation with pretty tracebacks
  - Built-in log rotation and retention
  - Easily switchable between JSON (production) and coloured text (dev)

Usage in any module:
    from app.core.logger import logger
    logger.info("SQL generated: {sql}", sql=sql_string)
"""

import sys
from pathlib import Path

from loguru import logger

from app.core.config import settings


def configure_logger() -> None:
    """
    Call once at application startup.
    Configures:
      - stderr sink  → human-readable, coloured (dev) or plain (prod)
      - file sink    → rotating JSON log in logs/
    """
    # Remove the default Loguru handler so we control everything
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # ── stderr sink ─────────────────────────────────────────────────────────
    logger.add(
        sys.stderr,
        format=log_format,
        level=settings.log_level,
        colorize=not settings.is_production,
        enqueue=True,            # thread-safe
    )

    # ── file sink (rotating) ────────────────────────────────────────────────
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        log_dir / "text_sql_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
        level=settings.log_level,
        rotation="00:00",        # new file every midnight
        retention="14 days",     # keep two weeks of logs
        compression="zip",       # compress old logs
        enqueue=True,
        serialize=settings.is_production,  # JSON in prod
    )

    logger.info(
        "Logger configured | env={env} | level={level} | log_dir={log_dir}",
        env=settings.app_env,
        level=settings.log_level,
        log_dir=str(log_dir),
    )


# ── Auto-configure on import ────────────────────────────────────────────────
configure_logger()
