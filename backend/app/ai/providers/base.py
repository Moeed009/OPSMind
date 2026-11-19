from __future__ import annotations

import abc
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.ai.exceptions import (
    AIAuthenticationError,
    AIError,
    AIMalformedResponseError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
    AIValidationError,
)
from app.ai.schemas.ai_schemas import ProviderRequest, ProviderResponse

logger = logging.getLogger("opsmind.ai.providers")


@dataclass
class CircuitBreaker:
    """Protects AI calls from cascading failures when a provider is unhealthy."""

    failure_threshold: int = 5
    cooldown_seconds: float = 60.0
    failure_count: int = 0
    opened_at: float | None = None

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if (time.monotonic() - self.opened_at) >= self.cooldown_seconds:
            return False
        return True

    def record_success(self) -> None:
        self.failure_count = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.opened_at = time.monotonic()

    def reset(self) -> None:
        self.failure_count = 0
        self.opened_at = None


class BaseAIProvider(abc.ABC):
    """Abstract base class for AI model providers."""

    provider_name: str = "base"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown_seconds: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_threshold,
            cooldown_seconds=circuit_breaker_cooldown_seconds,
        )
        self._external_client = http_client is not None
        self._client: httpx.AsyncClient | None = http_client

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def get_http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds))
            self._external_client = False
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._external_client and not self._client.is_closed:
            await self._client.aclose()

    @abc.abstractmethod
    def supports_model(self, model: str) -> bool:
        """Returns True if this provider supports the given model identifier."""

    @abc.abstractmethod
    async def _send_request(self, request: ProviderRequest) -> ProviderResponse:
        """Performs a single provider API call without retry wrapping."""

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Executes a provider request with circuit breaker, correlation ID, and exponential backoff."""
        correlation_id = request.correlation_id or uuid.uuid4().hex[:16]
        req = request.model_copy(update={"correlation_id": correlation_id})

        if not self.is_configured:
            raise AIAuthenticationError(
                f"Provider '{self.provider_name}' is not configured with an API key.",
                provider=self.provider_name,
                model=req.model,
                correlation_id=correlation_id,
            )

        if self.circuit_breaker.is_open():
            raise AIProviderUnavailableError(
                f"Circuit breaker is open for provider '{self.provider_name}'.",
                provider=self.provider_name,
                model=req.model,
                correlation_id=correlation_id,
            )

        attempt = 0
        last_error: AIError | None = None
        max_attempts = max(1, self.max_retries + 1)

        while attempt < max_attempts:
            attempt += 1
            try:
                response = await self._send_request(req)
                self.circuit_breaker.record_success()
                return response
            except (AIAuthenticationError, AIMalformedResponseError, AIValidationError) as non_retryable:
                logger.warning(
                    "Non-retryable AI error provider=%s model=%s correlation_id=%s error=%s",
                    self.provider_name,
                    req.model,
                    correlation_id,
                    non_retryable.message,
                )
                raise
            except (AIRateLimitError, AITimeoutError, AIProviderUnavailableError) as retryable_err:
                last_error = retryable_err
                self.circuit_breaker.record_failure()
                if attempt >= max_attempts or self.circuit_breaker.is_open():
                    break
                delay = (
                    retryable_err.retry_after_seconds
                    if isinstance(retryable_err, AIRateLimitError) and retryable_err.retry_after_seconds
                    else min(8.0, 0.25 * (2 ** (attempt - 1)))
                )
                logger.info(
                    "Retrying AI request provider=%s model=%s attempt=%d/%d delay=%.2fs correlation_id=%s",
                    self.provider_name,
                    req.model,
                    attempt,
                    max_attempts,
                    delay,
                    correlation_id,
                )
                await asyncio.sleep(delay)
            except AIError:
                raise
            except Exception as exc:
                self.circuit_breaker.record_failure()
                raise AIProviderUnavailableError(
                    f"Unexpected provider failure ({self.provider_name}): {exc}",
                    provider=self.provider_name,
                    model=req.model,
                    correlation_id=correlation_id,
                    cause=exc,
                ) from exc

        assert last_error is not None
        raise last_error

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)

    def redact_metadata(self, meta: dict[str, Any]) -> dict[str, Any]:
        """Strips any accidental key/authorization fields from metadata before logging."""
        forbidden = {"api_key", "apikey", "authorization", "x-goog-api-key", "x-api-key", "secret"}
        return {k: v for k, v in meta.items() if k.lower() not in forbidden}
