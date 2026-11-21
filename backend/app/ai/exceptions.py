"""Exceptions for the OpsMind AI integration layer."""
from __future__ import annotations


class AIError(Exception):
    """Base exception for all AI provider, routing, guardrail, and validation errors."""

    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        correlation_id: str | None = None,
        retryable: bool | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.correlation_id = correlation_id
        if retryable is not None:
            self.retryable = retryable
        self.cause = cause


class AIAuthenticationError(AIError):
    """Raised when provider API credentials are missing, invalid, or revoked."""

    retryable = False


class AIRateLimitError(AIError):
    """Raised when the AI provider returns HTTP 429 or quota exhaustion."""

    retryable = True

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        provider: str | None = None,
        model: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            provider=provider,
            model=model,
            correlation_id=correlation_id,
            retryable=True,
        )
        self.retry_after_seconds = retry_after_seconds


class AITimeoutError(AIError):
    """Raised when a provider request exceeds the configured timeout."""

    retryable = True


class AIProviderUnavailableError(AIError):
    """Raised when a provider is unconfigured, circuit-broken, or returning 5xx errors."""

    retryable = True


class AIMalformedResponseError(AIError):
    """Raised when the provider response is empty, non-JSON when JSON is required, or structurally invalid."""

    retryable = False


class AIValidationError(AIError):
    """Raised when model output fails schema or business rule validation."""

    retryable = False


class AIGuardrailBlockedError(AIError):
    """Raised when input or output security guardrails block an AI operation."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        rule_id: str | None = None,
        decision: str | None = None,
        layer: str = "input",
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message, correlation_id=correlation_id, retryable=False)
        self.rule_id = rule_id
        self.decision = decision
        self.layer = layer


class AIUsageLimitExceededError(AIError):
    """Raised when a tenant exceeds its configured monthly AI cost cap."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        tenant_id: str | None = None,
        current_spend_usd: str | None = None,
        limit_usd: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message, correlation_id=correlation_id, retryable=False)
        self.tenant_id = tenant_id
        self.current_spend_usd = current_spend_usd
        self.limit_usd = limit_usd
