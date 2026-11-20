"""Application service: the layer FastAPI routes call. Pure Python, no web/DB imports.

Responsibilities: permission checks, tenant isolation, OAuth start/finish (upsert + encrypt),
revoke, list, health, manual poll, sanitized audit entries.
"""
from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from . import crypto
from .base import ConnectionStatus, Connector, OAuth2Connector, TokenSet
from .errors import AccountAlreadyBound, ConnectorError, Forbidden, InvalidOAuthState, NotFound
from .ingestion import ConnectionRepo, ConnectionState, PollResult, poll_connection
from .oauth import (
    STATE_TTL_SECONDS,
    OAuthStateStore,
    StateRecord,
    complete_authorization,
    sign_state,
    start_authorization,
    verify_state,
)

CONNECTIONS_READ = "CONNECTIONS_READ"
CONNECTIONS_MANAGE = "CONNECTIONS_MANAGE"

@dataclass(frozen=True)
class Actor:
    tenant_id: str
    user_id: str
    permissions: frozenset[str]

    def require(self, perm: str) -> None:
        if perm not in self.permissions:
            raise Forbidden(f"missing permission {perm}")

@dataclass
class ConnectionView:
    """Safe projection for API responses. Never contains credentials."""
    id: str
    provider: str
    external_account_id: str | None
    status: ConnectionStatus
    last_error: str | None = None
    last_synced_at: str | None = None
    department_id: str | None = None

class AdminRepo(ConnectionRepo, Protocol):
    async def find_by_account(self, tenant_id: str, provider: str, account_id: str) -> str | None:
        """Existing connection id for UNIQUE (tenant_id, provider, external_account_id)."""

    async def upsert_credentials(
        self, connection_id: str, tenant_id: str, provider: str, account_id: str,
        ciphertext: str, key_id: str, department_id: str | None = None,
    ) -> None:
        """INSERT or UPDATE: status=CONNECTED, last_error=NULL. Must NOT touch sync_cursor."""

    async def get_for_tenant(self, tenant_id: str, connection_id: str) -> ConnectionState | None: ...

    async def get_view(self, tenant_id: str, connection_id: str) -> ConnectionView | None: ...

    async def list_for_tenant(self, tenant_id: str) -> list[ConnectionView]: ...

    async def mark_revoked(self, connection_id: str, tenant_id: str | None = None) -> None:
        """status=REVOKED, credentials_ciphertext=NULL, credentials_key_id=NULL. Row is kept."""

    async def delete(self, connection_id: str, tenant_id: str | None = None) -> None:
        """Permanently delete connection row."""

AuditFn = Callable[[str, str, str | None, dict], None]                                         

class ConnectionService:
    def __init__(
        self, *, repo: AdminRepo, state_store: OAuthStateStore, keyring: crypto.KeyRing,
        connector_factory: Callable[[str], Connector], state_secret: str, redirect_uri: str,
        audit: AuditFn, clock: Callable[[], float] = time.time,
    ):
        self.repo, self.store, self.keyring = repo, state_store, keyring
        self.factory, self.secret, self.redirect_uri = connector_factory, state_secret, redirect_uri
        self.audit, self.clock = audit, clock

    async def start_oauth(self, actor: Actor, provider: str, department_id: str | None = None) -> str:
        actor.require(CONNECTIONS_MANAGE)
        connector = self._oauth_connector(provider)
        return await start_authorization(
            connector, tenant_id=actor.tenant_id, user_id=actor.user_id, store=self.store,
            state_secret=self.secret, redirect_uri=self.redirect_uri, department_id=department_id, clock=self.clock,
        )

    async def issue_composio_state(
        self, actor: Actor, provider: str, department_id: str | None = None
    ) -> str:
        """Issue a signed, single-use OAuth state bound to tenant, user, provider, and department."""
        actor.require(CONNECTIONS_MANAGE)
        nonce = secrets.token_urlsafe(32)
        exp = self.clock() + STATE_TTL_SECONDS
        await self.store.save(
            StateRecord(
                nonce=nonce,
                tenant_id=str(actor.tenant_id),
                user_id=str(actor.user_id),
                provider=provider,
                code_verifier="composio",
                expires_at=exp,
            )
        )
        return sign_state(
            nonce=nonce,
            provider=provider,
            secret=self.secret,
            exp=exp,
            tenant_id=str(actor.tenant_id),
            department_id=department_id,
        )

    async def verify_composio_state(self, state: str) -> tuple[str, str, str, str | None]:
        """Verify and atomically consume a Composio OAuth state.

        Returns (tenant_id, user_id, provider, department_id) derived strictly from the
        signed state and server-side state store.
        """
        claims = verify_state(state, secret=self.secret, clock=self.clock)
        tid = claims.get("tid")
        if tid and hasattr(self.store, "hint_tenant"):
            self.store.hint_tenant(claims["nonce"], str(tid))
        rec = await self.store.consume(claims["nonce"])
        if rec is None or rec.provider != claims["provider"] or rec.code_verifier != "composio":
            raise InvalidOAuthState("state already used or expired")
        if tid and str(tid) != str(rec.tenant_id):
            raise InvalidOAuthState("tenant mismatch")
        return rec.tenant_id, rec.user_id, rec.provider, claims.get("did")

    async def _resolve_account_conflict(
        self, tenant_id: str, provider: str, account_id: str, department_id: str | None
    ) -> str | None:
        if hasattr(self.repo, "get_account_binding"):
            binding = await self.repo.get_account_binding(tenant_id, provider, account_id)
            if binding is not None:
                existing_id, existing_status, existing_dept_id = binding
                if existing_status != ConnectionStatus.REVOKED:
                    norm_existing = str(existing_dept_id) if existing_dept_id else None
                    norm_new = str(department_id) if department_id else None
                    if norm_existing != norm_new:
                        raise AccountAlreadyBound(
                            "This external account is already connected to another department."
                        )
                return existing_id
            return None
        return await self.repo.find_by_account(tenant_id, provider, account_id)

    async def finish_oauth(self, code: str, state: str) -> ConnectionView:
        """Callback entry. No Actor: identity comes from the server-side state row."""
        claims = verify_state(state, secret=self.secret, clock=self.clock)
        provider = claims["provider"]
        department_id = claims.get("did")
        connector = self._oauth_connector(provider)
        res = await complete_authorization(
            connector, code=code, state=state, store=self.store, state_secret=self.secret,
            redirect_uri=self.redirect_uri, clock=self.clock,
        )
        acct = res.account
        existing = await self._resolve_account_conflict(
            res.tenant_id, res.provider, acct.external_account_id, department_id
        )
        connection_id = existing or str(uuid.uuid4())                                      
        ct, key_id = crypto.encrypt(
            acct.tokens.to_dict(), tenant_id=res.tenant_id, connection_id=connection_id, keyring=self.keyring
        )
        await self.repo.upsert_credentials(
            connection_id, res.tenant_id, res.provider, acct.external_account_id, ct, key_id,
            department_id=department_id,
        )
        self.audit(
            "connection_updated" if existing else "connection_created", res.tenant_id, res.user_id,
            {"connection_id": connection_id, "provider": res.provider,
             "external_account_id": acct.external_account_id, "department_id": department_id},
        )
        view = await self.repo.get_view(res.tenant_id, connection_id)
        assert view is not None
        return view

    async def direct_connect(
        self,
        actor: Actor,
        provider: str,
        account_id: str,
        credentials: dict,
        department_id: str | None = None,
    ) -> ConnectionView:
        """Direct connection via API key or pre-issued token without OAuth redirection."""
        actor.require(CONNECTIONS_MANAGE)
        clean_account_id = (account_id or "").strip()
        if not clean_account_id:
            raise ConnectorError("external_account_id is required")
        raw_token = str(
            credentials.get("access_token")
            or credentials.get("api_key")
            or credentials.get("token")
            or ""
        ).strip()
        if not raw_token:
            raise ConnectorError("credentials must include a non-empty access_token, api_key, or token")
        tokens = TokenSet(
            access_token=raw_token,
            refresh_token=credentials.get("refresh_token"),
            scope=credentials.get("scope"),
        )
        existing = await self._resolve_account_conflict(
            actor.tenant_id, provider, clean_account_id, department_id
        )
        connection_id = existing or str(uuid.uuid4())
        ct, key_id = crypto.encrypt(
            tokens.to_dict(), tenant_id=actor.tenant_id, connection_id=connection_id, keyring=self.keyring
        )
        await self.repo.upsert_credentials(
            connection_id,
            actor.tenant_id,
            provider,
            clean_account_id,
            ct,
            key_id,
            department_id=department_id,
        )
        self.audit(
            "connection_updated" if existing else "connection_created",
            actor.tenant_id,
            actor.user_id,
            {
                "connection_id": connection_id,
                "provider": provider,
                "external_account_id": clean_account_id,
                "department_id": department_id,
            },
        )
        view = await self.repo.get_view(actor.tenant_id, connection_id)
        assert view is not None
        return view

    async def list(self, actor: Actor) -> list[ConnectionView]:
        actor.require(CONNECTIONS_READ)
        return await self.repo.list_for_tenant(actor.tenant_id)

    async def health(self, actor: Actor, connection_id: str) -> ConnectionView:
        actor.require(CONNECTIONS_READ)
        view = await self.repo.get_view(actor.tenant_id, connection_id)
        if view is None:
            raise NotFound("connection not found")
        return view

    async def poll(self, actor: Actor, connection_id: str) -> PollResult:
        actor.require(CONNECTIONS_MANAGE)
        state = await self._owned(actor, connection_id)
        result = await poll_connection(
            self.repo, self.factory(state.provider), self.keyring, connection_id, now=self.clock
        )
        self.audit("connection_polled", actor.tenant_id, actor.user_id, {
            "connection_id": connection_id, "status": result.status.value,
            "fetched": result.fetched, "inserted": result.inserted,
        })
        if result.status == ConnectionStatus.NEEDS_REAUTH:
            self.audit("connection_reauth_required", actor.tenant_id, actor.user_id,
                       {"connection_id": connection_id})
        return result

    async def revoke(self, actor: Actor, connection_id: str) -> None:
        actor.require(CONNECTIONS_MANAGE)
        state = await self._owned(actor, connection_id)
        if state.status == ConnectionStatus.REVOKED:
            return              
        if state.credentials_ciphertext:
            try:                                                                   
                tokens = TokenSet.from_dict(crypto.decrypt_json(
                    state.credentials_ciphertext, state.credentials_key_id,
                    tenant_id=state.tenant_id, connection_id=state.id, keyring=self.keyring))
                await self.factory(state.provider).revoke(tokens)
            except Exception:  # noqa: BLE001
                pass
        await self.repo.mark_revoked(connection_id, tenant_id=actor.tenant_id)
        self.audit("connection_revoked", actor.tenant_id, actor.user_id,
                   {"connection_id": connection_id, "provider": state.provider})

    async def delete(self, actor: Actor, connection_id: str) -> None:
        actor.require(CONNECTIONS_MANAGE)
        state = await self._owned(actor, connection_id)
        await self.repo.delete(connection_id, tenant_id=actor.tenant_id)
        self.audit("connection_deleted", actor.tenant_id, actor.user_id,
                   {"connection_id": connection_id, "provider": state.provider})

    async def _owned(self, actor: Actor, connection_id: str) -> ConnectionState:
        state = await self.repo.get_for_tenant(actor.tenant_id, connection_id)
        if state is None:                                                               
            raise NotFound("connection not found")
        return state

    def _oauth_connector(self, provider: str) -> OAuth2Connector:
        c = self.factory(provider)
        if not isinstance(c, OAuth2Connector):
            raise NotFound(f"provider '{provider}' does not use OAuth")
        return c
