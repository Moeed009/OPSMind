from __future__ import annotations

from contextlib import asynccontextmanager

from ..base import ConnectionStatus, Event
from ..ingestion import ConnectionState
from ..service import ConnectionView


class InMemoryConnectionRepo:
    def __init__(self):
        self.conns: dict[str, ConnectionState] = {}
        self.events: dict[tuple[str, str], Event] = {}                          
        self.locked: set[str] = set()
        self.last_error: dict[str, str | None] = {}
        self.retry_after: dict[str, float | None] = {}
        self.fail_commit = False

    def add(self, state: ConnectionState) -> None:
        self.conns[state.id] = state

    @asynccontextmanager
    async def lock(self, connection_id):
        if connection_id in self.locked:
            yield False
            return
        self.locked.add(connection_id)
        try:
            yield True
        finally:
            self.locked.discard(connection_id)

    async def load(self, connection_id):
        c = self.conns[connection_id]
        return ConnectionState(**c.__dict__)

    async def save_credentials(self, connection_id, ciphertext, key_id):
        c = self.conns[connection_id]
        c.credentials_ciphertext, c.credentials_key_id = ciphertext, key_id

    async def commit_batch(self, connection_id, tenant_id, rows, cursor):
        if self.fail_commit:
            raise RuntimeError("simulated crash before commit")                   
        inserted = 0
        for key, ev in rows:
            if (tenant_id, key) not in self.events:
                self.events[(tenant_id, key)] = ev
                inserted += 1
        c = self.conns[connection_id]
        c.sync_cursor = cursor if cursor is not None else c.sync_cursor
        c.status = ConnectionStatus.CONNECTED
        self.last_error[connection_id] = None
        return inserted

    async def set_status(self, connection_id, status, last_error=None, retry_after=None):
        self.conns[connection_id].status = status
        self.last_error[connection_id] = last_error
        self.retry_after[connection_id] = retry_after

    async def find_by_account(self, tenant_id, provider, account_id):
        for c in self.conns.values():
            if (c.tenant_id, c.provider, c.external_account_id) == (tenant_id, provider, account_id):
                return c.id
        return None

    async def get_account_binding(self, tenant_id, provider, account_id):
        for c in self.conns.values():
            if (c.tenant_id, c.provider, c.external_account_id) == (tenant_id, provider, account_id):
                return (c.id, c.status, getattr(c, "department_id", None))
        return None

    async def upsert_credentials(
        self,
        connection_id,
        tenant_id,
        provider,
        account_id,
        ciphertext,
        key_id,
        *,
        department_id: str | None = None,
    ):
        c = self.conns.get(connection_id)
        if c is None:
            st = ConnectionState(
                connection_id, tenant_id, provider, ConnectionStatus.CONNECTED,
                ciphertext, key_id, None, account_id)
            st.department_id = department_id  # type: ignore[attr-defined]
            self.conns[connection_id] = st
        else:
            c.credentials_ciphertext, c.credentials_key_id = ciphertext, key_id
            c.status = ConnectionStatus.CONNECTED
            if department_id is not None:
                c.department_id = department_id  # type: ignore[attr-defined]
        self.last_error[connection_id] = None

    async def get_for_tenant(self, tenant_id, connection_id):
        c = self.conns.get(connection_id)
        return ConnectionState(**c.__dict__) if c and c.tenant_id == tenant_id else None

    def _view(self, c):
        return ConnectionView(c.id, c.provider, c.external_account_id, c.status,
                              self.last_error.get(c.id))

    async def get_view(self, tenant_id, connection_id):
        c = self.conns.get(connection_id)
        return self._view(c) if c and c.tenant_id == tenant_id else None

    async def list_for_tenant(self, tenant_id, *, department_id: str | None = None):
        del department_id
        return [self._view(c) for c in self.conns.values() if c.tenant_id == tenant_id]

    async def mark_revoked(self, connection_id, tenant_id=None):
        c = self.conns.get(connection_id)
        if not c or (tenant_id is not None and c.tenant_id != tenant_id):
            return
        c.status, c.credentials_ciphertext, c.credentials_key_id = ConnectionStatus.REVOKED, None, None

    async def delete(self, connection_id, tenant_id=None):
        c = self.conns.get(connection_id)
        if not c or (tenant_id is not None and c.tenant_id != tenant_id):
            return
        self.conns.pop(connection_id, None)
