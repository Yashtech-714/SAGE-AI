"""
app/core/config.py
==================
Centralised application settings loaded from environment variables / .env file.

Why pydantic-settings?
  - Provides automatic type coercion (str -> int, bool, etc.)
  - Raises clear validation errors at startup -- fail-fast principle
  - Makes every setting explicit and self-documenting
  - Trivial to override in tests via environment variables
"""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration for the Text-to-SQL system."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM Provider (provider-agnostic) ────────────────────────────────────
    #
    # Architecture Decision: we use the OpenAI Python SDK with a configurable
    # base_url.  This is the industry-standard pattern for provider-agnostic
    # LLM clients -- Grok, Together.ai, Anyscale, Fireworks all expose
    # OpenAI-compatible /v1/chat/completions endpoints.
    #
    # Switch providers by changing two env vars -- zero code changes.
    #
    llm_provider: str = Field(
        "openai",
        description="LLM provider: openai | grok | together | fireworks",
    )
    llm_api_key: str = Field(
        "",
        description="Generic LLM API key (overrides openai_api_key if set)",
    )
    llm_base_url: str = Field(
        "",
        description="OpenAI-compatible base URL (e.g. https://api.x.ai/v1 for Grok)",
    )
    llm_model: str = Field(
        "gpt-4o-mini",
        description="Model identifier (gpt-4o-mini | grok-3-mini | grok-3)",
    )
    llm_temperature: float = Field(
        0.0,
        description="LLM temperature. 0.0 = deterministic SQL generation",
    )
    llm_max_tokens: int = Field(
        1024,
        description="Max tokens for SQL generation response",
    )

    # ── Backward-compat OpenAI key ───────────────────────────────────────────
    openai_api_key: str = Field(
        "",
        description="OpenAI secret key (used if llm_api_key is not set)",
    )

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(
        "sqlite:///./datasets/olist.db",
        description="SQLAlchemy-compatible database URL",
    )
    dataset_dir: Path = Field(
        Path("./Dataset"),
        description="Directory containing the raw Olist CSV files",
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_env: str = Field("development", description="development | production")
    log_level: str = Field("INFO", description="Python logging level")
    log_dir: Path = Field(Path("./logs"), description="Directory for log files")

    # ── SQL Safety ───────────────────────────────────────────────────────────
    max_query_retries: int = Field(
        3, description="Maximum retry attempts on failed SQL generation"
    )
    max_result_rows: int = Field(
        500, description="Hard cap on query result rows"
    )

    # ── Context / Token Budget ───────────────────────────────────────────────
    max_context_tables: int = Field(
        6, description="Max tables injected into LLM prompt (token efficiency)"
    )

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def db_path(self) -> Path:
        url = self.database_url
        if url.startswith("sqlite:///"):
            return Path(url.replace("sqlite:///", ""))
        return Path("./datasets/olist.db")

    @property
    def resolved_api_key(self) -> str:
        """llm_api_key takes priority over legacy openai_api_key."""
        return self.llm_api_key or self.openai_api_key

    @property
    def resolved_base_url(self) -> str | None:
        """None means use the SDK default (api.openai.com)."""
        url = self.llm_base_url.strip()
        return url if url else None

    @property
    def has_api_key(self) -> bool:
        return bool(self.resolved_api_key)


# ── Singleton ────────────────────────────────────────────────────────────────
settings = Settings()  # type: ignore[call-arg]
