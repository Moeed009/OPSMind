from __future__ import annotations

import asyncio
from typing import Any

from app.ai.exceptions import (
    AIAuthenticationError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.ai.providers.dept_providers import AIProviderInterface, ProviderResult
from app.ai.rate_limit import RedisRateLimiter
from app.core.config import get_settings


class DepartmentAIRouter:
    """Routes AI calls across Groq (primary) and Gemini (fallback) with per-provider rate limiting."""

    def __init__(
        self,
        providers: dict[str, AIProviderInterface],
        rate_limiter: RedisRateLimiter | None = None,
        retry_backoff_seconds: float = 0.05,
    ) -> None:
        self._providers = providers
        self._limiter = rate_limiter or RedisRateLimiter()
        self._retry_backoff = retry_backoff_seconds

    def _rate_limit_for(self, provider_name: str) -> int:
        settings = get_settings()
        if provider_name == "groq":
            return settings.groq_rate_limit_rpm
        if provider_name == "gemini":
            return settings.gemini_rate_limit_rpm
        return 30

    def _candidate_order(self, requested_provider: str) -> list[str]:
        settings = get_settings()
        if requested_provider == "auto":
            primary = settings.ai_primary_provider
            fallback = settings.ai_fallback_provider
            order = [primary]
            if fallback != primary:
                order.append(fallback)
            return order
        return [requested_provider]

    async def route(
        self,
        *,
        provider: str = "auto",
        system_prompt: str,
        user_content: str,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        model_override: str | None = None,
    ) -> ProviderResult:
        settings = get_settings()
        effective_max_tokens = max_tokens if max_tokens is not None else settings.ai_max_output_tokens
        effective_timeout = timeout if timeout is not None else settings.ai_request_timeout_seconds

        candidates = self._candidate_order(provider)
        configured = [
            name
            for name in candidates
            if name in self._providers and self._providers[name].is_configured()
        ]
        if not configured:
            if provider != "auto":
                raise AIProviderUnavailableError(
                    f"Provider '{provider}' is not configured.", provider=provider
                )
            raise AIProviderUnavailableError("No AI provider is configured.")

        last_err: Exception | None = None
        for name in configured:
            impl = self._providers[name]
            for attempt in range(2):
                try:
                    self._limiter.acquire(f"provider:{name}", self._rate_limit_for(name))
                    return await impl.generate(
                        system_prompt=system_prompt,
                        user_content=user_content,
                        response_schema=response_schema,
                        max_tokens=effective_max_tokens,
                        timeout=effective_timeout,
                        model_override=model_override,
                    )
                except AIAuthenticationError:
                    raise
                except (AIRateLimitError, AITimeoutError, AIProviderUnavailableError) as exc:
                    last_err = exc
                    if attempt == 0:
                        await asyncio.sleep(self._retry_backoff)
                        continue
                    break

        assert last_err is not None
        raise last_err
