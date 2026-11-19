"""Postgres implementation of AdminRepo using raw SQL (SQLAlchemy 2.x async).

Adapts the Phase 3 connector repository to the existing Section 8 database schema:
  * connections.credentials_ciphertext is BYTEA in the ORM model; this repo encodes/decodes base64 text.
  * connections.status uses the lowercase db.enums.ConnectionStatus values; this repo maps to/from
    connectors.base.ConnectionStatus values (CONNECTED, NEEDS_REAUTH, ...).
  * connections.last_health_check_at is mapped as last_synced_at; oauth_states is ensured on first use.
  * events is unique on (tenant_id, connection_id, dedup_key).
Connect with the SERVICE role / table owner (bypasses RLS); tenant filters are explicit in SQL.

CAVEAT: session-level advisory locks do NOT work through a transaction-mode pooler
(e.g. Supabase pgbouncer on port 6543). Use the direct/session connection for the API, or
replace lock() with: UPDATE connections SET lock_until = now() + interval '2 min'
WHERE id=:id AND (lock_until IS NULL OR lock_until < now()) RETURNING id.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .base import ConnectionStatus
from .ingestion import ConnectionState
from .service import ConnectionView

_TO_DB_STATUS: dict[ConnectionStatus, str] = {
    ConnectionStatus.CONNECTED: "healthy",
    ConnectionStatus.NEEDS_REAUTH: "pending",
    ConnectionStatus.RATE_LIMITED: "degraded",
    ConnectionStatus.ERROR: "error",
    ConnectionStatus.REVOKED: "revoked",
}

_FROM_DB_STATUS: dict[str, ConnectionStatus] = {
    "healthy": ConnectionStatus.CONNECTED,
    "pending": ConnectionStatus.NEEDS_REAUTH,
    "degraded": ConnectionStatus.RATE_LIMITED,
    "error": ConnectionStatus.ERROR,
    "revoked": ConnectionStatus.REVOKED,
    **{s.value: s for s in ConnectionStatus},
}

_VIEW_COLS = (
    "id, provider, external_account_id, status, last_error, "
    "last_health_check_at AS last_synced_at, department_id"
)

def _to_db_cipher(ciphertext: str | bytes | None) -> bytes | None:
    if ciphertext is None:
        return None
    return ciphertext.encode("ascii") if isinstance(ciphertext, str) else bytes(ciphertext)

def _from_db_cipher(raw: bytes | memoryview | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return bytes(raw).decode("ascii")

def _view(r) -> ConnectionView:
    dept_id = getattr(r, "department_id", None)
    return ConnectionView(
        str(r.id),
        r.provider,
        r.external_account_id,
        _FROM_DB_STATUS.get(r.status, ConnectionStatus.ERROR),
        r.last_error,
        r.last_synced_at.isoformat() if r.last_synced_at else None,
        str(dept_id) if dept_id else None,
    )

async def _set_tenant(s: AsyncSession, tenant_id: str | None) -> None:
    if tenant_id:
        await s.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(tenant_id)},
        )


class SqlConnectionRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.sf = session_factory
        self._tenants: dict[str, str] = {}

    @asynccontextmanager
    async def lock(self, connection_id: str):
        async with self.sf() as s:
            got = (await s.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:id))"), {"id": connection_id}
            )).scalar()
            try:
                yield bool(got)
            finally:
                if got:
                    await s.execute(
                        text("SELECT pg_advisory_unlock(hashtext(:id))"), {"id": connection_id}
                    )
                await s.commit()

    async def load(self, connection_id: str, tenant_id: str | None = None) -> ConnectionState:
        tid = str(tenant_id) if tenant_id else self._tenants.get(str(connection_id))
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            r = (await s.execute(text(
                "SELECT id, tenant_id, provider, status, credentials_ciphertext, credentials_key_id, "
                "sync_cursor, external_account_id FROM connections WHERE id = :id"),
                {"id": connection_id},
            )).one()
        self._tenants[str(r.id)] = str(r.tenant_id)
        return ConnectionState(
            str(r.id),
            str(r.tenant_id),
            r.provider,
            _FROM_DB_STATUS.get(r.status, ConnectionStatus.ERROR),
            _from_db_cipher(r.credentials_ciphertext),
            r.credentials_key_id or "",
            r.sync_cursor,
            r.external_account_id,
        )

    async def get_for_tenant(self, tenant_id, connection_id):
        tid = str(tenant_id)
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            r = (await s.execute(text(
                "SELECT id, tenant_id, provider, status, credentials_ciphertext, credentials_key_id, "
                "sync_cursor, external_account_id FROM connections WHERE id = :id AND tenant_id = :t"),
                {"id": connection_id, "t": tid})).first()
        if not r:
            return None
        self._tenants[str(r.id)] = str(r.tenant_id)
        return ConnectionState(
            str(r.id),
            str(r.tenant_id),
            r.provider,
            _FROM_DB_STATUS.get(r.status, ConnectionStatus.ERROR),
            _from_db_cipher(r.credentials_ciphertext),
            r.credentials_key_id or "",
            r.sync_cursor,
            r.external_account_id,
        )

    async def save_credentials(self, connection_id, ciphertext, key_id):
        tid = self._tenants.get(str(connection_id))
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            await s.execute(text(
                "UPDATE connections SET credentials_ciphertext=:c, credentials_key_id=:k, "
                "updated_at=now() WHERE id=:id AND (CAST(:t AS uuid) IS NULL OR tenant_id=:t)"),
                {"c": _to_db_cipher(ciphertext), "k": key_id, "id": connection_id, "t": tid})

    async def commit_batch(self, connection_id, tenant_id, rows, cursor):
        tid = str(tenant_id)
        self._tenants[str(connection_id)] = tid
        inserted = 0
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            dept_row = (
                await s.execute(
                    text("SELECT department_id FROM connections WHERE id = :id AND tenant_id = :t"),
                    {"id": connection_id, "t": tid},
                )
            ).first()
            dept_id = str(dept_row.department_id) if (dept_row and dept_row.department_id) else None
            for key, ev in rows:
                payload = dict(ev.payload) if isinstance(ev.payload, dict) else {"data": ev.payload}
                if ev.occurred_at and "occurred_at" not in payload:
                    payload["occurred_at"] = ev.occurred_at
                res = await s.execute(text(
                    "INSERT INTO events (tenant_id, connection_id, department_id, event_type, dedup_key, payload) "
                    "VALUES (:t, :c, :d, :et, :dk, CAST(:p AS jsonb)) "
                    "ON CONFLICT (tenant_id, connection_id, dedup_key) DO NOTHING"),
                    {"t": tid, "c": connection_id, "d": dept_id, "et": ev.event_type, "dk": key,
                     "p": json.dumps(payload)})
                inserted += res.rowcount or 0
            await s.execute(text(
                "UPDATE connections SET sync_cursor = COALESCE(:cur, sync_cursor), "
                "last_health_check_at = now(), updated_at = now(), "
                "status = :st, last_error = NULL "
                "WHERE id = :id AND tenant_id = :t"),
                {"cur": cursor, "st": _TO_DB_STATUS[ConnectionStatus.CONNECTED], "id": connection_id, "t": tid})
        return inserted

    async def set_status(self, connection_id, status, last_error=None, retry_after=None):
        del retry_after
        tid = self._tenants.get(str(connection_id))
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            await s.execute(text(
                "UPDATE connections SET status=:st, last_error=:e, "
                "last_health_check_at=now(), updated_at=now() "
                "WHERE id=:id AND (CAST(:t AS uuid) IS NULL OR tenant_id=:t)"),
                {"st": _TO_DB_STATUS.get(status, "error"), "e": last_error, "id": connection_id, "t": tid})

    async def find_by_account(self, tenant_id, provider, account_id):
        tid = str(tenant_id)
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            r = (await s.execute(text(
                "SELECT id FROM connections WHERE tenant_id=:t AND provider=:p AND external_account_id=:a"),
                {"t": tid, "p": provider, "a": account_id})).first()
        if r:
            self._tenants[str(r.id)] = tid
            return str(r.id)
        return None

    async def get_account_binding(self, tenant_id, provider, account_id):
        tid = str(tenant_id)
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            r = (await s.execute(text(
                "SELECT id, status, department_id FROM connections "
                "WHERE tenant_id=:t AND provider=:p AND external_account_id=:a"),
                {"t": tid, "p": provider, "a": account_id})).first()
        if r:
            self._tenants[str(r.id)] = tid
            return (
                str(r.id),
                _FROM_DB_STATUS.get(r.status, ConnectionStatus.ERROR),
                str(r.department_id) if r.department_id else None,
            )
        return None

    async def upsert_credentials(
        self,
        connection_id,
        tenant_id,
        provider,
        account_id,
        ciphertext,
        key_id,
        department_id=None,
    ):
        tid = str(tenant_id)
        self._tenants[str(connection_id)] = tid
        display_name = f"{provider} ({account_id})" if account_id else provider
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            await s.execute(text(
                "INSERT INTO connections (id, tenant_id, department_id, provider, display_name, external_account_id, "
                "credentials_ciphertext, credentials_key_id, status) "
                "VALUES (:id, :t, :d, :p, :dn, :a, :c, :k, :st) "
                "ON CONFLICT (tenant_id, provider, external_account_id) DO UPDATE SET "
                "department_id = COALESCE(EXCLUDED.department_id, connections.department_id), "
                "credentials_ciphertext = EXCLUDED.credentials_ciphertext, "
                "credentials_key_id = EXCLUDED.credentials_key_id, "
                "status = EXCLUDED.status, last_error = NULL, updated_at = now()"),
                {
                    "id": connection_id,
                    "t": tid,
                    "d": str(department_id) if department_id else None,
                    "p": provider,
                    "dn": display_name,
                    "a": account_id,
                    "c": _to_db_cipher(ciphertext),
                    "k": key_id,
                    "st": _TO_DB_STATUS[ConnectionStatus.CONNECTED],
                })

    async def get_view(self, tenant_id, connection_id):
        tid = str(tenant_id)
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            r = (await s.execute(text(
                f"SELECT {_VIEW_COLS} FROM connections WHERE id=:id AND tenant_id=:t"),
                {"id": connection_id, "t": tid})).first()
        return _view(r) if r else None

    async def list_for_tenant(self, tenant_id):
        tid = str(tenant_id)
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            rows = (await s.execute(text(
                f"SELECT {_VIEW_COLS} FROM connections WHERE tenant_id=:t AND provider != 'generic' ORDER BY provider, external_account_id"),
                {"t": tid})).all()
        return [_view(r) for r in rows]

    async def mark_revoked(self, connection_id, tenant_id=None):
        tid = str(tenant_id) if tenant_id else self._tenants.get(str(connection_id))
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            await s.execute(text(
                "UPDATE connections SET status=:st, credentials_ciphertext=NULL, "
                "credentials_key_id=NULL, updated_at=now() "
                "WHERE id=:id AND (CAST(:t AS uuid) IS NULL OR tenant_id=:t)"),
                {"st": _TO_DB_STATUS[ConnectionStatus.REVOKED], "id": connection_id, "t": tid})

    async def delete(self, connection_id: str, tenant_id=None):
        import uuid as _uuid

        tid = str(tenant_id) if tenant_id else self._tenants.get(str(connection_id))
        params = {"id": connection_id, "t": tid}
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            owned = (await s.execute(text(
                "SELECT tenant_id FROM connections WHERE id=:id AND (CAST(:t AS uuid) IS NULL OR tenant_id=:t)"),
                params)).first()
            if not owned:
                return
            eff_tid = str(owned.tenant_id)

            # Pause dependent published WorkflowVersions BEFORE nulling trigger_connection_id,
            # inside the exact same transaction.
            pub_rows = (await s.execute(
                text(
                    "SELECT wv.id AS vid, wv.workflow_id, wv.trigger_connection_id, wv.definition, w.name AS wf_name "
                    "FROM workflow_versions wv "
                    "JOIN workflows w ON w.id = wv.workflow_id "
                    "WHERE wv.tenant_id = :t AND wv.status = 'published'"
                ),
                {"t": eff_tid},
            )).all()
            for r in pub_rows:
                matches_trigger = str(r.trigger_connection_id or "") == str(connection_id)
                matches_node = False
                if not matches_trigger and isinstance(r.definition, dict):
                    for node in r.definition.get("nodes") or []:
                        if isinstance(node, dict):
                            cfg = node.get("config") or {}
                            if isinstance(cfg, dict) and str(cfg.get("connection_id") or "") == str(connection_id):
                                matches_node = True
                                break
                if matches_trigger or matches_node:
                    await s.execute(
                        text(
                            "UPDATE workflow_versions SET status = 'paused', pause_reason = 'connection deleted' "
                            "WHERE id = :vid AND tenant_id = :t"
                        ),
                        {"vid": str(r.vid), "t": eff_tid},
                    )
                    await s.execute(
                        text(
                            "INSERT INTO audit_logs (id, tenant_id, action, entity_type, entity_id, details) "
                            "VALUES (:aid, :t, 'workflow.auto_paused', 'workflow_version', :eid, CAST(:details AS jsonb))"
                        ),
                        {
                            "aid": str(_uuid.uuid4()),
                            "t": eff_tid,
                            "eid": str(r.vid),
                            "details": json.dumps({
                                "workflow_id": str(r.workflow_id),
                                "workflow_name": r.wf_name,
                                "connection_id": str(connection_id),
                                "reason": "connection deleted",
                            }),
                        },
                    )

            await s.execute(
                text(
                    "UPDATE runs SET event_id = NULL "
                    "WHERE event_id IN (SELECT id FROM events WHERE connection_id = :id)"
                ),
                {"id": connection_id},
            )
            await s.execute(
                text("UPDATE workflow_versions SET trigger_connection_id = NULL WHERE trigger_connection_id = :id"),
                {"id": connection_id},
            )
            await s.execute(
                text("UPDATE documents SET connection_id = NULL WHERE connection_id = :id"),
                {"id": connection_id},
            )
            await s.execute(text("DELETE FROM action_logs WHERE connection_id=:id"), {"id": connection_id})
            await s.execute(text("DELETE FROM events WHERE connection_id=:id"), {"id": connection_id})
            await s.execute(text("DELETE FROM connections WHERE id=:id"), {"id": connection_id})
        self._tenants.pop(str(connection_id), None)

class SqlOAuthStateStore:
    """Implements the async OAuthStateStore protocol from oauth.py."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.sf = session_factory
        self._nonce_tenants: dict[str, str] = {}

    def hint_tenant(self, nonce: str, tenant_id: str) -> None:
        self._nonce_tenants[nonce] = str(tenant_id)

    async def save(self, rec):
        tid = str(rec.tenant_id)
        self._nonce_tenants[rec.nonce] = tid
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            await s.execute(text(
                "INSERT INTO oauth_states (nonce, tenant_id, user_id, provider, code_verifier, expires_at) "
                "VALUES (:n, :t, :u, :p, :v, to_timestamp(:e))"),
                {"n": rec.nonce, "t": tid, "u": rec.user_id, "p": rec.provider,
                 "v": rec.code_verifier, "e": rec.expires_at})

    async def consume(self, nonce):
        tid = self._nonce_tenants.pop(nonce, None)
        async with self.sf.begin() as s:
            await _set_tenant(s, tid)
            r = (await s.execute(text(
                "UPDATE oauth_states SET consumed_at = now() "
                "WHERE nonce=:n AND consumed_at IS NULL AND expires_at > now() "
                "RETURNING nonce, tenant_id, user_id, provider, code_verifier, "
                "extract(epoch from expires_at) AS exp"), {"n": nonce})).first()
        if not r:
            return None
        from .oauth import StateRecord
        return StateRecord(r.nonce, str(r.tenant_id), str(r.user_id), r.provider, r.code_verifier, float(r.exp))
