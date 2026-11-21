
from __future__ import annotations

import random
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from . import crypto
from .base import ConnectionStatus, Connector, Event, EventBatch, TokenSet, dedup_key
from .errors import (
    ConnectionRevoked,
    ConnectorError,
    CursorExpired,
    PollInProgress,
    RateLimited,
    ReauthRequired,
    TokenExpired,
)

REFRESH_SKEW_SECONDS = 60

@dataclass
class ConnectionState:
    id: str
    tenant_id: str
    provider: str
    status: ConnectionStatus
    credentials_ciphertext: str | None
    credentials_key_id: str | None
    sync_cursor: str | None
    external_account_id: str | None = None
    department_id: str | None = None

class ConnectionRepo(Protocol):
    def lock(self, connection_id: str) -> AbstractAsyncContextManager[bool]:
        """Yields True if lock acquired, False if another poll holds it.
        Postgres: pg_try_advisory_xact_lock(hashtext(connection_id))."""

    async def load(self, connection_id: str) -> ConnectionState: ...

    async def save_credentials(self, connection_id: str, ciphertext: str, key_id: str) -> None: ...

    async def commit_batch(
        self, connection_id: str, tenant_id: str, rows: list[tuple[str, Event]], cursor: str | None
    ) -> int:
        """ONE transaction: INSERT events ON CONFLICT (tenant_id, dedup_key) DO NOTHING,
        update sync_cursor + last_synced_at, status=CONNECTED, clear last_error.
        Returns number of newly inserted events."""

    async def set_status(
        self, connection_id: str, status: ConnectionStatus,
        last_error: str | None = None, retry_after: float | None = None,
    ) -> None: ...

@dataclass
class PollResult:
    status: ConnectionStatus
    fetched: int = 0
    inserted: int = 0
    full_resync: bool = False
    error: str | None = None
    retry_after: float | None = None

def backoff_delay(attempt: int, *, base: float = 2.0, cap: float = 900.0,
                  rng: Callable[[], float] = random.random) -> float:
    
    return rng() * min(cap, base * (2 ** attempt))

async def poll_connection(
    repo: ConnectionRepo,
    connector: Connector,
    keyring: crypto.KeyRing,
    connection_id: str,
    *,
    now: Callable[[], float] = time.time,
) -> PollResult:
    async with repo.lock(connection_id) as acquired:
        if not acquired:
            raise PollInProgress("poll already running for this connection")

        
        state = await repo.load(connection_id)
        if state.status == ConnectionStatus.REVOKED or not state.credentials_ciphertext:
            raise ConnectionRevoked("connection is revoked")

        aad = dict(tenant_id=state.tenant_id, connection_id=state.id, keyring=keyring)
        tokens = TokenSet.from_dict(
            crypto.decrypt_json(state.credentials_ciphertext, state.credentials_key_id, **aad)
        )

        async def persist(t: TokenSet) -> None:
            ct, kid = crypto.encrypt(t.to_dict(), **aad)
            await repo.save_credentials(state.id, ct, kid)

        try:
            if crypto.needs_rotation(state.credentials_key_id, keyring):
                await persist(tokens)                                    

            if tokens.expires_at is not None and tokens.expires_at - now() < REFRESH_SKEW_SECONDS:
                tokens = await connector.refresh(tokens)
                await persist(tokens)

            try:
                batch = await connector.fetch_events(tokens, state.sync_cursor)
            except TokenExpired:                                                     
                tokens = await connector.refresh(tokens)
                await persist(tokens)
                batch = await connector.fetch_events(tokens, state.sync_cursor)
            except CursorExpired:
                batch = EventBatch([], None, reset_required=True)

            full_resync = False
            if batch.reset_required:                                                                
                full_resync = True
                batch = await connector.fetch_events(tokens, None)

            rows = [(dedup_key(state.id, state.provider, e.external_id), e) for e in batch.events]
            inserted = await repo.commit_batch(state.id, state.tenant_id, rows, batch.next_cursor)
            return PollResult(ConnectionStatus.CONNECTED, len(rows), inserted, full_resync)

        except ReauthRequired:
            await repo.set_status(state.id, ConnectionStatus.NEEDS_REAUTH, "re-authorization required")
            return PollResult(ConnectionStatus.NEEDS_REAUTH, error="re-authorization required")
        except RateLimited as e:
            await repo.set_status(state.id, ConnectionStatus.RATE_LIMITED, "rate limited", e.retry_after)
            return PollResult(ConnectionStatus.RATE_LIMITED, error="rate limited", retry_after=e.retry_after)
        except ConnectorError as e:
            msg = str(e)[:200]                                                                
            await repo.set_status(state.id, ConnectionStatus.ERROR, msg)
            return PollResult(ConnectionStatus.ERROR, error=msg)
