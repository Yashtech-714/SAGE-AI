"""
app/services/llm_service.py
==============================
Provider-agnostic LLM client.

Architecture Decision: OpenAI SDK as Universal Interface
---------------------------------------------------------
The OpenAI Python SDK is the de-facto standard for LLM APIs.
Virtually every major provider exposes an OpenAI-compatible endpoint:

    Provider         Base URL                          Model Examples
    --------         --------                          --------------
    OpenAI           https://api.openai.com/v1         gpt-4o-mini, gpt-4o
    Grok (X.AI)      https://api.x.ai/v1               grok-3-mini, grok-3
    Together.ai      https://api.together.xyz/v1       Llama-3, Mistral
    Fireworks.ai     https://api.fireworks.ai/inference llama-v3p1-70b
    Groq             https://api.groq.com/openai/v1    llama3-70b-8192
    Ollama (local)   http://localhost:11434/v1         llama3, codellama

By configuring only base_url + api_key, we get a SINGLE client that
works with ALL of these -- zero code changes required.

This is exactly how Databricks DBRX, LangChain, and LlamaIndex implement
multi-provider support.

LLM Response Design
--------------------
LLMResponse is a dataclass (not Pydantic) because:
  - It's internal to the AI pipeline, not serialized to JSON
  - Dataclasses are faster to construct (no validation overhead)
  - Pydantic models are used at API boundaries only (clean separation)
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from app.core.config import settings
from app.core.logger import logger


# ── Response Model ────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Structured output from any LLM call."""
    content: str                    # The raw text response
    model: str                      # Which model was used
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = "stop"     # stop | length | content_filter

    @property
    def was_truncated(self) -> bool:
        """True if the model stopped due to token limit, not natural end."""
        return self.finish_reason == "length"


# ── Provider Protocol (Dependency Injection Interface) ────────────────────────

@runtime_checkable
class LLMProvider(Protocol):
    """
    Protocol (structural interface) for any LLM provider.

    WHY Protocol over ABC?
      Protocol uses structural subtyping (duck typing) -- any class that
      implements these methods satisfies the interface without explicitly
      inheriting from it. This makes mocking in tests trivially easy:

          class MockProvider:
              async def complete(self, messages, **kwargs): ...

      No base class import needed. This is the preferred pattern in modern
      Python for dependency injection.
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse: ...

    async def health_check(self) -> bool: ...


# ── OpenAI-Compatible Provider ────────────────────────────────────────────────

class OpenAICompatibleProvider:
    """
    Single LLM client that works with OpenAI, Grok, Together, Groq, Fireworks,
    and any other OpenAI-compatible provider.

    Configuration via .env:
        LLM_PROVIDER=grok
        LLM_API_KEY=xai-...
        LLM_BASE_URL=https://api.x.ai/v1
        LLM_MODEL=grok-3-mini

        LLM_PROVIDER=openai
        LLM_API_KEY=sk-...
        LLM_MODEL=gpt-4o-mini
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        provider_name: str = "openai",
    ) -> None:
        self._model = model
        self._provider = provider_name

        # The magic: same SDK, different base_url
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,          # None => api.openai.com (default)
            timeout=60.0,
            max_retries=2,              # SDK-level network retry (not our logic retry)
        )

        logger.info(
            "LLMProvider initialised | provider={p} | model={m} | base_url={url}",
            p=provider_name,
            m=model,
            url=base_url or "api.openai.com",
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """
        Call the chat completion endpoint.

        WHY temperature=0.0 for SQL?
          SQL generation requires determinism. A temperature > 0 introduces
          randomness that causes different SQL on identical questions -- bad
          for analytics reproducibility. We use 0.0 as the default and let
          callers override for tasks that benefit from creativity (insight
          generation uses 0.3).
        """
        logger.debug(
            "LLM request | provider={p} | model={m} | messages={n} | temp={t}",
            p=self._provider,
            m=self._model,
            n=len(messages),
            t=temperature,
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,          # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
            )

            choice = response.choices[0]
            usage  = response.usage

            result = LLMResponse(
                content=choice.message.content or "",
                model=response.model,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                finish_reason=choice.finish_reason or "stop",
            )

            logger.info(
                "LLM response | tokens={total} (prompt={p} + completion={c}) | finish={f}",
                total=result.total_tokens,
                p=result.prompt_tokens,
                c=result.completion_tokens,
                f=result.finish_reason,
            )

            return result

        except RateLimitError as e:
            logger.error("LLM rate limit exceeded: {e}", e=str(e))
            raise
        except APITimeoutError as e:
            logger.error("LLM request timed out: {e}", e=str(e))
            raise
        except APIError as e:
            logger.error("LLM API error | status={s} | {e}", s=e.status_code, e=str(e))
            raise

    async def health_check(self) -> bool:
        """Quick ping to verify the API is reachable."""
        try:
            resp = await self.complete(
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=5,
            )
            return bool(resp.content)
        except Exception as e:
            logger.warning("LLM health check failed: {e}", e=str(e))
            return False


# ── Mock Provider (for tests without an API key) ─────────────────────────────

class MockLLMProvider:
    """
    Deterministic mock for testing the pipeline without API calls.

    WHY include a mock in production code?
      Test isolation is a production engineering principle.
      Unit tests that make real API calls are:
        - Slow (100ms+ per call)
        - Expensive (costs money)
        - Brittle (fail when API is down)
      A mock lets us test every part of the pipeline except the LLM step.
    """

    def __init__(self, default_response: str = "SELECT * FROM orders LIMIT 10") -> None:
        self._default_response = default_response
        self.call_count = 0
        self.last_messages: list[dict] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.call_count += 1
        self.last_messages = messages
        return LLMResponse(
            content=self._default_response,
            model="mock",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        )

    async def health_check(self) -> bool:
        return True


# ── Factory ───────────────────────────────────────────────────────────────────

def create_llm_provider(
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    provider_name: str | None = None,
) -> OpenAICompatibleProvider:
    """
    Factory for creating the LLM provider from settings.
    Accepts overrides for testing.
    """
    return OpenAICompatibleProvider(
        api_key=api_key or settings.resolved_api_key,
        model=model or settings.llm_model,
        base_url=base_url or settings.resolved_base_url,
        provider_name=provider_name or settings.llm_provider,
    )


# ── Module-level singleton (lazy) ─────────────────────────────────────────────
_provider: OpenAICompatibleProvider | None = None


def get_llm_provider() -> OpenAICompatibleProvider:
    """Return the shared LLM provider instance."""
    global _provider
    if _provider is None:
        _provider = create_llm_provider()
    return _provider
