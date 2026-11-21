"""FakeConnector: same flow as a real OAuth provider, no network, deterministic tokens."""
from __future__ import annotations

import hashlib

from ..base import (
    ConnectionStatus,
    Event,
    EventBatch,
    Health,
    OAuth2Connector,
    TokenSet,
)
from ..errors import CursorExpired, ReauthRequired
from ..registry import register


@register("fake")
class FakeConnector(OAuth2Connector):
    client_id = "fake-client"
    auth_endpoint = "https://fake.example/authorize"
    token_endpoint = "https://fake.example/token"
    scopes = ["fake.read"]
    extra_auth_params = {"access_type": "offline", "prompt": "consent"}
    account_id = "fake-user@example.com"

    reject_refresh = False
    expired_cursor = False
    pending_events: list[Event] = []

    def __init__(self):
        self.revoked = False
        self.last_code_verifier: str | None = None

    async def exchange_code(self, code, code_verifier, redirect_uri):
        self.last_code_verifier = code_verifier
        h = hashlib.sha256(code.encode()).hexdigest()[:12]
        return TokenSet(f"fake-access-{h}", f"fake-refresh-{h}", None, "fake.read")

    async def fetch_account_id(self, tokens):
        return self.account_id

    async def refresh(self, tokens):
        if self.reject_refresh:
            raise ReauthRequired("invalid_grant")
        return TokenSet(tokens.access_token + "+", tokens.refresh_token, None, tokens.scope)

    async def fetch_events(self, tokens, since_cursor):
        if self.expired_cursor and since_cursor:
            raise CursorExpired("cursor too old")
        events = list(self.pending_events)
        return EventBatch(events, next_cursor=str(int(since_cursor or 0) + len(events)))

    async def revoke(self, tokens):
        self.revoked = True

    async def health(self, tokens):
        return Health(ConnectionStatus.CONNECTED)
