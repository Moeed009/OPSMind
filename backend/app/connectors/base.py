"""Connector interfaces. `Connector` is for every provider; `OAuth2Connector` adds the OAuth bits."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar
from urllib.parse import urlencode


class ConnectionStatus(StrEnum):
    CONNECTED = "CONNECTED"
    NEEDS_REAUTH = "NEEDS_REAUTH"
    RATE_LIMITED = "RATE_LIMITED"
    ERROR = "ERROR"
    REVOKED = "REVOKED"

@dataclass
class Event:
    external_id: str
    event_type: str
    occurred_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

@dataclass
class EventBatch:
    events: list[Event]
    next_cursor: str | None = None
    reset_required: bool = False                                                     

@dataclass
class Health:
    status: ConnectionStatus
    last_error: str | None = None
    retry_after: float | None = None

@dataclass
class TokenSet:
    """Decrypted credential payload. Only ever persisted via crypto.encrypt()."""
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None                
    scope: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> TokenSet:
        return cls(**{k: d.get(k) for k in ("access_token", "refresh_token", "expires_at", "scope")})

@dataclass
class ConnectedAccount:
    external_account_id: str
    tokens: TokenSet

def dedup_key(connection_id, provider: str, external_id: str) -> str:
    """Tenant-scoped via connection_id; DB also enforces UNIQUE (tenant_id, dedup_key)."""
    return f"{connection_id}:{provider}:{external_id}"

class Connector(ABC):
    provider: str = ""
    max_requests_per_minute: int = 60

    @abstractmethod
    async def refresh(self, tokens: TokenSet) -> TokenSet:
        """Return fresh tokens (no-op for API-key providers). Raise ReauthRequired on invalid_grant."""

    @abstractmethod
    async def fetch_events(self, tokens: TokenSet, since_cursor: str | None) -> EventBatch: ...

    @abstractmethod
    async def revoke(self, tokens: TokenSet) -> None:
        """Best-effort provider-side revocation."""

    @abstractmethod
    async def health(self, tokens: TokenSet) -> Health: ...

    async def handle_webhook(self, headers: dict[str, str], body: bytes) -> list[Event]:
        """Optional. Implementations MUST verify the provider signature before parsing."""
        raise NotImplementedError(f"{self.provider} does not support webhooks")

class OAuth2Connector(Connector):
    scopes: ClassVar[list[str]] = []
    auth_endpoint: str = ""
    token_endpoint: str = ""
    revoke_endpoint: str | None = None
    client_id: str = ""
    extra_auth_params: ClassVar[dict[str, str]] = {}

    def authorize_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            **self.extra_auth_params,
        }
        return f"{self.auth_endpoint}?{urlencode(params)}"

    @abstractmethod
    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet: ...

    @abstractmethod
    async def fetch_account_id(self, tokens: TokenSet) -> str:
        """e.g. Gmail address via users.getProfile."""

    async def handle_callback(self, code: str, code_verifier: str, redirect_uri: str) -> ConnectedAccount:
        tokens = await self.exchange_code(code, code_verifier, redirect_uri)
        return ConnectedAccount(await self.fetch_account_id(tokens), tokens)
