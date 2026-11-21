
from __future__ import annotations

import os

import httpx

from app.ai.exceptions import AIProviderUnavailableError
from app.ai.providers.base import BaseAIProvider
from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.groq import GroqProvider
from app.core.config import get_settings


class ProviderRegistry:
    """Central registry managing configured free-tier AI provider instances (Groq & Gemini)."""

    def __init__(
        self,
        *,
        gemini_api_key: str | None = None,
        groq_api_key: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        timeout = timeout_seconds if timeout_seconds is not None else settings.ai_request_timeout_seconds
        retries = max_retries if max_retries is not None else settings.ai_max_retries
        cb_thresh = settings.ai_circuit_breaker_threshold
        cb_cooldown = settings.ai_circuit_breaker_cooldown_seconds

        explicit_keys_provided = any(k is not None for k in (gemini_api_key, groq_api_key))
        resolved_gemini_key = (
            gemini_api_key
            if gemini_api_key is not None
            else ("" if explicit_keys_provided else (os.getenv("GEMINI_API_KEY") or settings.gemini_api_key))
        )
        resolved_groq_key = (
            groq_api_key
            if groq_api_key is not None
            else ("" if explicit_keys_provided else (os.getenv("GROQ_API_KEY") or settings.groq_api_key))
        )

        self._providers: dict[str, BaseAIProvider] = {
            "gemini": GeminiProvider(
                api_key=resolved_gemini_key,
                timeout_seconds=timeout,
                max_retries=retries,
                circuit_breaker_threshold=cb_thresh,
                circuit_breaker_cooldown_seconds=cb_cooldown,
                http_client=http_client,
            ),
            "groq": GroqProvider(
                api_key=resolved_groq_key,
                timeout_seconds=timeout,
                max_retries=retries,
                circuit_breaker_threshold=cb_thresh,
                circuit_breaker_cooldown_seconds=cb_cooldown,
                http_client=http_client,
            ),
        }

    def register(self, name: str, provider: BaseAIProvider) -> None:
        self._providers[name.lower().strip()] = provider

    def get(self, name: str) -> BaseAIProvider:
        key = name.lower().strip()
        provider = self._providers.get(key)
        if provider is None:
            raise AIProviderUnavailableError(f"Unknown AI provider '{name}'.", provider=name)
        return provider

    def is_available(self, name: str) -> bool:
        key = name.lower().strip()
        provider = self._providers.get(key)
        if provider is None:
            return False
        return provider.is_configured and not provider.circuit_breaker.is_open()

    def available_providers(self) -> list[str]:
        return [name for name, p in self._providers.items() if p.is_configured and not p.circuit_breaker.is_open()]

    async def aclose_all(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
