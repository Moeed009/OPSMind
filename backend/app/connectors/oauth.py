"""Generic OAuth orchestration: PKCE, signed state (JWS), single-use server-side nonce.

The server-side OAuthStateStore row is the trust anchor (tenant + user recorded while the
user was authenticated). The JWS only carries nonce/provider/exp.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import jwt

from .base import ConnectedAccount, OAuth2Connector
from .errors import InvalidOAuthState

STATE_TTL_SECONDS = 600

def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)                                     

def code_challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

@dataclass
class StateRecord:
    nonce: str
    tenant_id: str
    user_id: str
    provider: str
    code_verifier: str
    expires_at: float
    consumed_at: float | None = None

class OAuthStateStore(Protocol):
    async def save(self, record: StateRecord) -> None: ...

    async def consume(self, nonce: str) -> StateRecord | None:
        """Atomically mark consumed and return the record, or None if missing/expired/consumed.
        SQL: UPDATE oauth_states SET consumed_at = now()
             WHERE nonce = $1 AND consumed_at IS NULL AND expires_at > now() RETURNING *"""

class InMemoryOAuthStateStore:
    """For unit tests / local dev only."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._rows: dict[str, StateRecord] = {}
        self._clock = clock

    async def save(self, record: StateRecord) -> None:
        self._rows[record.nonce] = record

    async def consume(self, nonce: str) -> StateRecord | None:
        rec = self._rows.get(nonce)
        if rec is None or rec.consumed_at is not None or rec.expires_at <= self._clock():
            return None
        rec.consumed_at = self._clock()
        return rec

def sign_state(
    *,
    nonce: str,
    provider: str,
    secret: str,
    exp: float,
    tenant_id: str | None = None,
    department_id: str | None = None,
) -> str:
    payload: dict[str, object] = {"nonce": nonce, "provider": provider, "exp": int(exp)}
    if tenant_id is not None:
        payload["tid"] = str(tenant_id)
    if department_id is not None:
        payload["did"] = str(department_id)
    return jwt.encode(payload, secret, algorithm="HS256")

def verify_state(token: str, *, secret: str, clock: Callable[[], float] = time.time) -> dict:
    """Verify signature and expiry (expiry checked against an injectable clock)."""
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"],
            options={"require": ["exp", "nonce", "provider"], "verify_exp": False},
        )
    except jwt.PyJWTError as exc:
        raise InvalidOAuthState("invalid state") from exc
    if claims["exp"] <= clock():
        raise InvalidOAuthState("state expired")
    return claims

async def start_authorization(
    connector: OAuth2Connector,
    *,
    tenant_id: str,
    user_id: str,
    store: OAuthStateStore,
    state_secret: str,
    redirect_uri: str,
    department_id: str | None = None,
    clock: Callable[[], float] = time.time,
) -> str:
    """Caller must already have verified CONNECTIONS_MANAGE. Returns the provider authorize URL."""
    nonce = secrets.token_urlsafe(32)
    verifier = new_code_verifier()
    exp = clock() + STATE_TTL_SECONDS
    await store.save(StateRecord(nonce, str(tenant_id), str(user_id), connector.provider, verifier, exp))
    state = sign_state(
        nonce=nonce,
        provider=connector.provider,
        secret=state_secret,
        exp=exp,
        tenant_id=str(tenant_id),
        department_id=department_id,
    )
    return connector.authorize_url(
        state=state, code_challenge=code_challenge_s256(verifier), redirect_uri=redirect_uri
    )

@dataclass
class CallbackResult:
    tenant_id: str
    user_id: str
    provider: str
    account: ConnectedAccount

async def complete_authorization(
    connector: OAuth2Connector,
    *,
    code: str,
    state: str,
    store: OAuthStateStore,
    state_secret: str,
    redirect_uri: str,
    clock: Callable[[], float] = time.time,
) -> CallbackResult:
    """Verify state, consume nonce (single use), exchange code. Persisting (upsert + encrypt +
    audit) is done by the service layer using the returned tenant/user/account."""
    claims = verify_state(state, secret=state_secret, clock=clock)
    if claims["provider"] != connector.provider:
        raise InvalidOAuthState("provider mismatch")
    tid = claims.get("tid")
    if tid and hasattr(store, "hint_tenant"):
        store.hint_tenant(claims["nonce"], str(tid))
    rec = await store.consume(claims["nonce"])
    if rec is None or rec.provider != connector.provider:
        raise InvalidOAuthState("state already used or expired")
    account = await connector.handle_callback(code, rec.code_verifier, redirect_uri)
    return CallbackResult(rec.tenant_id, rec.user_id, rec.provider, account)
