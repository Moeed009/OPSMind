"""Connector error hierarchy. Messages must never contain tokens or raw provider payloads."""

class ConnectorError(Exception):
    """Base error for all connector failures."""

class CryptoError(ConnectorError):
    """Encryption/decryption failure (bad key, tampered data, wrong AAD)."""

class ReauthRequired(ConnectorError):
    """Provider rejected the refresh token (e.g. invalid_grant). Status -> NEEDS_REAUTH."""

class RateLimited(ConnectorError):
    """Provider returned 429. Status -> RATE_LIMITED."""

    def __init__(self, retry_after: float | None = None):
        super().__init__("rate limited by provider")
        self.retry_after = retry_after

class CursorExpired(ConnectorError):
    """Sync cursor no longer valid; caller must perform a bounded full resync."""

class InvalidOAuthState(ConnectorError):
    """OAuth state missing, expired, tampered, or already consumed."""

class UnknownProvider(ConnectorError):
    """No connector registered for the requested provider."""

class TokenExpired(ConnectorError):
    """Access token rejected (401). Caller refreshes once and retries."""

class PollInProgress(ConnectorError):
    """Another poll holds the per-connection lock (HTTP 409)."""

class ConnectionRevoked(ConnectorError):
    """Connection is REVOKED; polling is not allowed."""

class Forbidden(ConnectorError):
    """Actor lacks the required permission."""

class NotFound(ConnectorError):
    """Connection does not exist for this tenant (also used for cross-tenant access)."""


class AccountAlreadyBound(ConnectorError):
    """External account is already actively connected to another department."""
