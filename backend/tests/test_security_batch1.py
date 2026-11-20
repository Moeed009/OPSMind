"""Batch 1 Security Regression Tests (F-01, F-02, F-03/F-04, F-05, F-13).

All tests in this file run purely in-memory or inside rolled-back transactions —
ZERO commits to the real database.
"""
from __future__ import annotations

import time
import urllib.parse
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_tenant_context
from app.api.v1 import connectors as connectors_api
from app.connectors import crypto
from app.connectors.base import ConnectionStatus as ConnStatus
from app.connectors.oauth import InMemoryOAuthStateStore, sign_state
from app.connectors.service import ConnectionService, ConnectionView
from app.db.enums import Role
from app.main import app


class _FakeAdminRepo:
    """In-memory AdminRepo for testing ConnectionService without any DB writes."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def find_by_account(self, tenant_id: str, provider: str, account_id: str) -> str | None:
        for cid, r in self.rows.items():
            if r["tenant_id"] == tenant_id and r["provider"] == provider and r["account_id"] == account_id:
                return cid
        return None

    async def upsert_credentials(
        self,
        connection_id: str,
        tenant_id: str,
        provider: str,
        account_id: str,
        ciphertext: str,
        key_id: str,
        department_id: str | None = None,
    ) -> None:
        self.rows[connection_id] = {
            "id": connection_id,
            "tenant_id": tenant_id,
            "provider": provider,
            "account_id": account_id,
            "ciphertext": ciphertext,
            "key_id": key_id,
            "department_id": department_id,
            "status": ConnStatus.CONNECTED,
        }

    async def get_view(self, tenant_id: str, connection_id: str) -> ConnectionView | None:
        r = self.rows.get(connection_id)
        if not r or r["tenant_id"] != tenant_id:
            return None
        return ConnectionView(
            id=r["id"],
            provider=r["provider"],
            external_account_id=r["account_id"],
            status=r["status"],
            department_id=r["department_id"],
        )

    async def list_for_tenant(self, tenant_id: str) -> list[ConnectionView]:
        return [
            ConnectionView(
                id=r["id"],
                provider=r["provider"],
                external_account_id=r["account_id"],
                status=r["status"],
                department_id=r["department_id"],
            )
            for r in self.rows.values()
            if r["tenant_id"] == tenant_id
        ]


def _make_inmemory_connection_service(secret: str = "test-secret-key-at-least-32-bytes-long-0001"):
    store = InMemoryOAuthStateStore()
    repo = _FakeAdminRepo()
    import base64

    keyring = crypto.KeyRing({"k1": base64.b64decode(crypto.generate_key())}, "k1")
    audits: list[tuple[str, str, str | None, dict]] = []
    svc = ConnectionService(
        repo=repo,  # type: ignore[arg-type]
        state_store=store,
        keyring=keyring,
        connector_factory=lambda p: None,  # type: ignore[arg-type]
        state_secret=secret,
        redirect_uri="http://testserver/api/v1/connections/oauth/callback",
        audit=lambda action, tid, uid, payload: audits.append((action, tid, uid, payload)),
    )
    return svc, repo, store, audits


class _FakeUser:
    def __init__(self, user_id: uuid.UUID) -> None:
        self.id = user_id
        self.email = "admin@example.com"


# ---------------------------------------------------------------------------
# F-01: Composio OAuth Callback — Signed, Single-Use State Verification
# ---------------------------------------------------------------------------


def test_composio_oauth_callback_rejects_missing_forged_and_replayed_state(monkeypatch) -> None:
    from app.services import composio_service
    from app.services import department as department_service

    secret = "composio-test-secret-32-bytes-minimum-key!"
    svc, repo, _store, audits = _make_inmemory_connection_service(secret=secret)

    real_tenant_id = uuid.uuid4()
    real_user_id = uuid.uuid4()
    real_dept_id = uuid.uuid4()
    attacker_tenant_id = uuid.uuid4()
    attacker_dept_id = uuid.uuid4()

    captured_callback_url: dict[str, str] = {}

    monkeypatch.setattr(composio_service, "is_composio_configured", lambda: True)
    monkeypatch.setattr(
        composio_service,
        "authorize_provider",
        lambda user_id, provider, callback_url=None: (
            captured_callback_url.update({"url": callback_url or ""})
            or {"authorize_url": f"https://composio.example/auth?cb={callback_url}"}
        ),
    )
    monkeypatch.setattr(
        composio_service,
        "get_toolkit_connection_state",
        lambda user_id, provider: {"is_active": True, "connected_account_id": "ca_verified_123"},
    )
    monkeypatch.setattr(
        department_service,
        "require_department_permission",
        lambda *args, **kwargs: None,
    )

    fake_tc = TenantContext(
        user=_FakeUser(real_user_id),  # type: ignore[arg-type]
        tenant_id=real_tenant_id,
        role=Role.OWNER,
        db=None,  # type: ignore[arg-type]
    )

    app.dependency_overrides[connectors_api.get_connection_service] = lambda: svc
    app.dependency_overrides[get_tenant_context] = lambda: fake_tc
    try:
        client = TestClient(app)

        # 1. Start Composio OAuth -> callback_url must carry signed state and NOT raw tenant_id/department_id
        r_start = client.post(
            f"/connections/oauth/start/gmail?department_id={real_dept_id}",
            headers={"Accept": "application/json"},
        )
        assert r_start.status_code == 200, r_start.text
        cb_url = captured_callback_url["url"]
        parsed = urllib.parse.urlparse(cb_url)
        qs = urllib.parse.parse_qs(parsed.query)
        assert "state" in qs and len(qs["state"][0]) > 20
        assert "tenant_id" not in qs
        assert "department_id" not in qs
        valid_state = qs["state"][0]

        # 2. Missing state -> rejected with 400
        r_missing = client.get(
            f"/connections/oauth/callback?composio=1&composio_provider=gmail&tenant_id={attacker_tenant_id}",
            headers={"Accept": "application/json"},
        )
        assert r_missing.status_code == 400, r_missing.text
        assert len(repo.rows) == 0

        # 3. Forged state (signed with wrong secret) -> rejected with 400
        forged_state = sign_state(
            nonce="forged-nonce-12345",
            provider="gmail",
            secret="wrong-attacker-secret-32-bytes-long!!",
            exp=time.time() + 600,
            tenant_id=str(attacker_tenant_id),
            department_id=str(attacker_dept_id),
        )
        r_forged = client.get(
            f"/connections/oauth/callback?composio=1&state={urllib.parse.quote(forged_state)}",
            headers={"Accept": "application/json"},
        )
        assert r_forged.status_code == 400, r_forged.text
        assert len(repo.rows) == 0

        # 4. Valid state + attacker-injected query params -> succeeds using ONLY verified state claims
        r_ok = client.get(
            f"/connections/oauth/callback?composio=1&state={urllib.parse.quote(valid_state)}"
            f"&tenant_id={attacker_tenant_id}&department_id={attacker_dept_id}&composio_provider=stripe",
            headers={"Accept": "application/json"},
        )
        assert r_ok.status_code == 200, r_ok.text
        assert len(repo.rows) == 1
        stored = next(iter(repo.rows.values()))
        assert stored["tenant_id"] == str(real_tenant_id)
        assert stored["department_id"] == str(real_dept_id)
        assert stored["provider"] == "gmail"
        assert audits[-1][2] == str(real_user_id)

        # 5. Replayed state -> rejected with 400
        r_replay = client.get(
            f"/connections/oauth/callback?composio=1&state={urllib.parse.quote(valid_state)}",
            headers={"Accept": "application/json"},
        )
        assert r_replay.status_code == 400, r_replay.text
        assert len(repo.rows) == 1
    finally:
        app.dependency_overrides.clear()
