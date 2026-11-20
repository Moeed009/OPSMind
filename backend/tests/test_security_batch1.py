"""Batch 1 Security Regression Tests (F-01, F-02, F-03/F-04, F-05, F-13).

All tests in this file run purely in-memory or inside rolled-back transactions —
ZERO commits to the real database.
"""
from __future__ import annotations

import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext, get_tenant_context
from app.api.v1 import connectors as connectors_api
from app.connectors import crypto
from app.connectors.base import ConnectionStatus as ConnStatus
from app.connectors.oauth import InMemoryOAuthStateStore, sign_state
from app.connectors.service import ConnectionService, ConnectionView
from app.db.enums import (
    ActionStatus,
    DepartmentRole,
    DepartmentStatus,
    DepartmentType,
    DocumentSource,
    DocumentStatus,
    Region,
    ReviewTaskStatus,
    Role,
    RunStatus,
    RunStepStatus,
    TriggerSource,
    WorkflowApprovalStatus,
    WorkflowVersionStatus,
)
from app.db.models import (
    ActionLog,
    Department,
    DepartmentMember,
    Document,
    ReviewTask,
    Run,
    RunStep,
    Tenant,
    User,
    Workflow,
    WorkflowApproval,
    WorkflowVersion,
)
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.main import app


@pytest.fixture()
def rolled_back_db():
    """Real Postgres session wrapped in an outer transaction that ALWAYS rolls back."""
    engine = get_engine()
    conn = engine.connect()
    outer_tx = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer_tx.rollback()
        conn.close()


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


# ---------------------------------------------------------------------------
# F-02: POST /connections/composio/execute — Allowlist, Approval Gate & Idempotency
# ---------------------------------------------------------------------------


def test_composio_execute_blocks_unapproved_and_money_moving_without_approval(
    rolled_back_db: Session, monkeypatch
) -> None:
    from app.services import composio_service

    tenant = Tenant(
        id=uuid.uuid4(),
        name="Composio Sec Tenant",
        slug=f"comp-sec-{uuid.uuid4().hex[:8]}",
        region=Region.US,
    )
    user = User(id=uuid.uuid4(), email=f"owner-{uuid.uuid4().hex[:8]}@example.com", full_name="Owner")
    set_rls_context(rolled_back_db, tenant_id=tenant.id, user_id=user.id)
    rolled_back_db.add_all([tenant, user])
    rolled_back_db.flush()

    finance = Department(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Finance",
        code="finance",
        department_type=DepartmentType.FINANCE,
        status=DepartmentStatus.ACTIVE,
    )
    rolled_back_db.add(finance)
    rolled_back_db.flush()

    wf = Workflow(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        department_id=finance.id,
        name="Refund Flow",
    )
    rolled_back_db.add(wf)
    rolled_back_db.flush()
    ver = WorkflowVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        workflow_id=wf.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={"trigger": {"type": "manual"}, "nodes": [], "edges": []},
    )
    rolled_back_db.add(ver)
    rolled_back_db.flush()
    run = Run(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        department_id=finance.id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        trigger_source=TriggerSource.MANUAL,
        status=RunStatus.WAITING_APPROVAL,
        trace_id=uuid.uuid4().hex,
        input={},
    )
    rolled_back_db.add(run)
    rolled_back_db.flush()

    step = RunStep(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        run_id=run.id,
        step_index=0,
        node_id="approve_1",
        step_type="approval",
        status=RunStepStatus.COMPLETED,
    )
    rolled_back_db.add(step)
    rolled_back_db.flush()

    # Pending (unapproved) ReviewTask — must NOT satisfy money-moving gate
    rolled_back_db.add(
        ReviewTask(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            run_id=run.id,
            run_step_id=step.id,
            status=ReviewTaskStatus.OPEN,
            risk_reason="Refund requires approval",
            proposal={"amount": 5000},
            deadline_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    rolled_back_db.flush()

    execute_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(composio_service, "is_composio_configured", lambda: True)
    monkeypatch.setattr(
        composio_service,
        "execute_tool",
        lambda user_id, tool_slug, arguments=None: (
            execute_calls.append({"user_id": user_id, "tool_slug": tool_slug, "arguments": arguments})
            or {"ok": True, "tool_slug": tool_slug, "refund_id": "re_123"}
        ),
    )

    tc = TenantContext(user=user, tenant_id=tenant.id, role=Role.OWNER, db=rolled_back_db)
    app.dependency_overrides[get_tenant_context] = lambda: tc
    try:
        client = TestClient(app)

        # 1. Unapproved provider tool -> 403
        r_unapproved = client.post(
            "/connections/composio/execute",
            json={
                "tool_slug": "PIPEDRIVE_CREATE_DEAL",
                "arguments": {"title": "Deal"},
                "department_id": str(finance.id),
            },
        )
        assert r_unapproved.status_code == 403, r_unapproved.text
        assert len(execute_calls) == 0

        # 2. Money-moving Stripe tool without run_id -> 403
        r_no_run = client.post(
            "/connections/composio/execute",
            json={
                "tool_slug": "STRIPE_CREATE_REFUND",
                "arguments": {"charge": "ch_1"},
                "department_id": str(finance.id),
            },
        )
        assert r_no_run.status_code == 403, r_no_run.text
        assert len(execute_calls) == 0

        # 3. Money-moving Stripe tool with run_id that only has OPEN ReviewTask -> 403
        r_unapproved_run = client.post(
            "/connections/composio/execute",
            json={
                "tool_slug": "STRIPE_CREATE_REFUND",
                "arguments": {"charge": "ch_1"},
                "department_id": str(finance.id),
                "run_id": str(run.id),
                "idempotency_key": "stripe-refund-key-1",
            },
        )
        assert r_unapproved_run.status_code == 403, r_unapproved_run.text
        assert len(execute_calls) == 0

        # 4. Approve via WorkflowApproval -> succeeds, writes ActionLog, and replays idempotently
        rolled_back_db.add(
            WorkflowApproval(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                department_id=finance.id,
                run_id=run.id,
                node_id="approve_1",
                required_role="reviewer",
                status=WorkflowApprovalStatus.APPROVED,
            )
        )
        rolled_back_db.flush()

        r_ok = client.post(
            "/connections/composio/execute",
            json={
                "tool_slug": "STRIPE_CREATE_REFUND",
                "arguments": {"charge": "ch_1"},
                "department_id": str(finance.id),
                "run_id": str(run.id),
                "idempotency_key": "stripe-refund-key-1",
            },
        )
        assert r_ok.status_code == 200, r_ok.text
        assert len(execute_calls) == 1

        log_row = rolled_back_db.scalar(
            select(ActionLog).where(
                ActionLog.tenant_id == tenant.id,
                ActionLog.idempotency_key == "stripe-refund-key-1",
            )
        )
        assert log_row is not None
        assert log_row.status == ActionStatus.SUCCEEDED

        # Second call with same idempotency_key -> replayed without calling Composio again
        r_replay = client.post(
            "/connections/composio/execute",
            json={
                "tool_slug": "STRIPE_CREATE_REFUND",
                "arguments": {"charge": "ch_1"},
                "department_id": str(finance.id),
                "run_id": str(run.id),
                "idempotency_key": "stripe-refund-key-1",
            },
        )
        assert r_replay.status_code == 200, r_replay.text
        assert len(execute_calls) == 1
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# F-03 / F-04: Knowledge Hub Department Scoping (GET /knowledge & DELETE)
# ---------------------------------------------------------------------------


def test_knowledge_list_and_delete_enforce_department_isolation(rolled_back_db: Session) -> None:
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Knowledge Sec Tenant",
        slug=f"kb-sec-{uuid.uuid4().hex[:8]}",
        region=Region.US,
    )
    owner = User(id=uuid.uuid4(), email=f"kb-owner-{uuid.uuid4().hex[:8]}@example.com", full_name="Owner")
    support_builder = User(
        id=uuid.uuid4(), email=f"kb-sup-{uuid.uuid4().hex[:8]}@example.com", full_name="Support Builder"
    )
    for u in (owner, support_builder):
        set_rls_context(rolled_back_db, user_id=u.id)
        rolled_back_db.add(u)
        rolled_back_db.flush()

    set_rls_context(rolled_back_db, tenant_id=tenant.id, user_id=owner.id)
    rolled_back_db.add(tenant)
    rolled_back_db.flush()

    support = Department(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Customer Support",
        code="support",
        department_type=DepartmentType.SUPPORT,
        status=DepartmentStatus.ACTIVE,
    )
    finance = Department(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Finance",
        code="finance",
        department_type=DepartmentType.FINANCE,
        status=DepartmentStatus.ACTIVE,
    )
    rolled_back_db.add_all([support, finance])
    rolled_back_db.flush()

    rolled_back_db.add(
        DepartmentMember(
            tenant_id=tenant.id,
            department_id=support.id,
            user_id=support_builder.id,
            role=DepartmentRole.BUILDER,
        )
    )
    rolled_back_db.flush()

    shared_doc = Document(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        department_id=None,
        scope="company_shared",
        source=DocumentSource.UPLOAD,
        title="Company Handbook",
        status=DocumentStatus.READY,
    )
    support_doc = Document(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        source=DocumentSource.UPLOAD,
        title="Support Runbook",
        status=DocumentStatus.READY,
    )
    finance_doc = Document(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        department_id=finance.id,
        scope="department_private",
        source=DocumentSource.UPLOAD,
        title="Finance Confidential POs",
        status=DocumentStatus.READY,
    )
    rolled_back_db.add_all([shared_doc, support_doc, finance_doc])
    rolled_back_db.flush()

    active_tc: dict[str, TenantContext] = {
        "tc": TenantContext(
            user=support_builder,
            tenant_id=tenant.id,
            role=Role.BUILDER,
            db=rolled_back_db,
        )
    }
    app.dependency_overrides[get_tenant_context] = lambda: active_tc["tc"]
    try:
        client = TestClient(app)

        # 1. GET /knowledge (without department_id) as Support builder -> sees ONLY company_shared + support_private
        r_sup_list = client.get("/knowledge")
        assert r_sup_list.status_code == 200, r_sup_list.text
        titles = {d["title"] for d in r_sup_list.json()}
        assert titles == {"Company Handbook", "Support Runbook"}
        assert "Finance Confidential POs" not in titles

        # 2. DELETE Finance private doc as Support builder -> 403
        r_del_fin = client.delete(f"/knowledge/{finance_doc.id}")
        assert r_del_fin.status_code == 403, r_del_fin.text

        # 3. DELETE company_shared doc as Support builder -> 403 (owner/admin only)
        r_del_shared = client.delete(f"/knowledge/{shared_doc.id}")
        assert r_del_shared.status_code == 403, r_del_shared.text

        # 4. DELETE Support private doc as Support builder -> 204
        r_del_sup = client.delete(f"/knowledge/{support_doc.id}")
        assert r_del_sup.status_code == 204, r_del_sup.text

        # 5. Switch to OWNER -> GET /knowledge sees remaining docs including Finance private doc
        active_tc["tc"] = TenantContext(
            user=owner,
            tenant_id=tenant.id,
            role=Role.OWNER,
            db=rolled_back_db,
        )
        r_own_list = client.get("/knowledge")
        assert r_own_list.status_code == 200, r_own_list.text
        own_titles = {d["title"] for d in r_own_list.json()}
        assert own_titles == {"Company Handbook", "Finance Confidential POs"}

        # 6. OWNER can delete company_shared doc -> 204
        r_own_del_shared = client.delete(f"/knowledge/{shared_doc.id}")
        assert r_own_del_shared.status_code == 204, r_own_del_shared.text
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# F-05: GET /reviews & POST /reviews/{id}/decide Department RBAC
# ---------------------------------------------------------------------------


def test_reviews_list_and_decide_enforce_department_reviewer_role(rolled_back_db: Session) -> None:
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Reviews Sec Tenant",
        slug=f"rev-sec-{uuid.uuid4().hex[:8]}",
        region=Region.US,
    )
    owner = User(id=uuid.uuid4(), email=f"rev-own-{uuid.uuid4().hex[:8]}@example.com", full_name="Owner")
    support_reviewer = User(
        id=uuid.uuid4(), email=f"rev-sup-{uuid.uuid4().hex[:8]}@example.com", full_name="Support Reviewer"
    )
    finance_viewer = User(
        id=uuid.uuid4(), email=f"rev-finv-{uuid.uuid4().hex[:8]}@example.com", full_name="Finance Viewer"
    )
    for u in (owner, support_reviewer, finance_viewer):
        set_rls_context(rolled_back_db, user_id=u.id)
        rolled_back_db.add(u)
        rolled_back_db.flush()

    set_rls_context(rolled_back_db, tenant_id=tenant.id, user_id=owner.id)
    rolled_back_db.add(tenant)
    rolled_back_db.flush()

    support = Department(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Customer Support",
        code="support",
        department_type=DepartmentType.SUPPORT,
        status=DepartmentStatus.ACTIVE,
    )
    finance = Department(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Finance",
        code="finance",
        department_type=DepartmentType.FINANCE,
        status=DepartmentStatus.ACTIVE,
    )
    rolled_back_db.add_all([support, finance])
    rolled_back_db.flush()

    rolled_back_db.add_all(
        [
            DepartmentMember(
                tenant_id=tenant.id,
                department_id=support.id,
                user_id=support_reviewer.id,
                role=DepartmentRole.REVIEWER,
            ),
            DepartmentMember(
                tenant_id=tenant.id,
                department_id=finance.id,
                user_id=finance_viewer.id,
                role=DepartmentRole.VIEWER,
            ),
        ]
    )
    rolled_back_db.flush()

    def _make_review_task(dept_id: uuid.UUID | None, name: str) -> ReviewTask:
        wf = Workflow(id=uuid.uuid4(), tenant_id=tenant.id, department_id=dept_id, name=name)
        rolled_back_db.add(wf)
        rolled_back_db.flush()
        ver = WorkflowVersion(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            workflow_id=wf.id,
            version_number=1,
            status=WorkflowVersionStatus.PUBLISHED,
            definition={"trigger": {"type": "manual"}, "nodes": [], "edges": []},
        )
        rolled_back_db.add(ver)
        rolled_back_db.flush()
        run = Run(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            department_id=dept_id,
            workflow_id=wf.id,
            workflow_version_id=ver.id,
            trigger_source=TriggerSource.MANUAL,
            status=RunStatus.WAITING_HUMAN,
            trace_id=uuid.uuid4().hex,
            input={},
        )
        rolled_back_db.add(run)
        rolled_back_db.flush()
        step = RunStep(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            run_id=run.id,
            step_index=0,
            node_id="review_1",
            step_type="approval",
            status=RunStepStatus.COMPLETED,
        )
        rolled_back_db.add(step)
        rolled_back_db.flush()
        rt = ReviewTask(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            run_id=run.id,
            run_step_id=step.id,
            status=ReviewTaskStatus.OPEN,
            risk_reason=f"Review for {name}",
            proposal={"note": name},
            deadline_at=datetime.now(UTC) + timedelta(hours=24),
        )
        rolled_back_db.add(rt)
        rolled_back_db.flush()
        return rt

    support_task = _make_review_task(support.id, "Support Flow")
    finance_task = _make_review_task(finance.id, "Finance Flow")
    legacy_task = _make_review_task(None, "Legacy Flow")

    active_tc: dict[str, TenantContext] = {
        "tc": TenantContext(
            user=support_reviewer,
            tenant_id=tenant.id,
            role=Role.REVIEWER,
            db=rolled_back_db,
        )
    }
    app.dependency_overrides[get_tenant_context] = lambda: active_tc["tc"]
    try:
        client = TestClient(app)

        # 1. Support reviewer lists reviews -> sees Support + Legacy read-only, NEVER Finance
        r_sup_list = client.get("/reviews?status=open")
        assert r_sup_list.status_code == 200, r_sup_list.text
        ids = {item["id"] for item in r_sup_list.json()}
        assert str(support_task.id) in ids
        assert str(legacy_task.id) in ids
        assert str(finance_task.id) not in ids

        # 2. Support reviewer CANNOT decide Finance task -> 403
        r_sup_fin = client.post(
            f"/reviews/{finance_task.id}/decide",
            json={"decision": "approved", "reason": "cross-dept attempt"},
        )
        assert r_sup_fin.status_code == 403, r_sup_fin.text

        # 3. Legacy task stays read-only -> 409
        r_leg = client.post(
            f"/reviews/{legacy_task.id}/decide",
            json={"decision": "approved", "reason": "legacy attempt"},
        )
        assert r_leg.status_code == 409, r_leg.text

        # 4. Support reviewer CAN decide Support task -> 200
        r_sup_ok = client.post(
            f"/reviews/{support_task.id}/decide",
            json={"decision": "approved", "reason": "looks good"},
        )
        assert r_sup_ok.status_code == 200, r_sup_ok.text
        assert r_sup_ok.json()["status"] == "approved"

        # 5. Finance member with VIEWER dept role cannot list or decide Finance task
        active_tc["tc"] = TenantContext(
            user=finance_viewer,
            tenant_id=tenant.id,
            role=Role.REVIEWER,
            db=rolled_back_db,
        )
        r_fv_list = client.get("/reviews?status=open&exclude_legacy=true")
        assert r_fv_list.status_code == 200, r_fv_list.text
        assert r_fv_list.json() == []

        r_fv_dec = client.post(
            f"/reviews/{finance_task.id}/decide",
            json={"decision": "approved", "reason": "viewer attempt"},
        )
        assert r_fv_dec.status_code == 403, r_fv_dec.text

        # 6. Company OWNER can list and decide Finance task -> 200
        active_tc["tc"] = TenantContext(
            user=owner,
            tenant_id=tenant.id,
            role=Role.OWNER,
            db=rolled_back_db,
        )
        r_own_list = client.get("/reviews?status=open&exclude_legacy=true")
        assert r_own_list.status_code == 200, r_own_list.text
        assert {item["id"] for item in r_own_list.json()} == {str(finance_task.id)}

        r_own_dec = client.post(
            f"/reviews/{finance_task.id}/decide",
            json={"decision": "approved", "reason": "owner approved"},
        )
        assert r_own_dec.status_code == 200, r_own_dec.text
        assert r_own_dec.json()["status"] == "approved"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# F-13 (Security): Fail-closed APP_DATABASE_URL & Active Department Check in Celery Task
# ---------------------------------------------------------------------------


def test_connectors_build_service_fails_closed_without_app_database_url(monkeypatch) -> None:
    from types import SimpleNamespace

    connectors_api._build_service.cache_clear()
    fake_settings = SimpleNamespace(
        database_url="postgresql+psycopg://postgres:secret@localhost:5432/postgres",
        app_database_url="",
        oauth_state_secret="secret-32-bytes-minimum-for-test-only!",
        connector_encryption_keys="",
        connector_encryption_current_key_id="",
    )
    monkeypatch.setattr(connectors_api, "get_settings", lambda: fake_settings)
    try:
        with pytest.raises(RuntimeError, match="APP_DATABASE_URL is not configured"):
            connectors_api._build_service()
    finally:
        connectors_api._build_service.cache_clear()


def test_execute_workflow_run_task_blocks_inactive_and_archived_department(
    rolled_back_db: Session, monkeypatch
) -> None:
    from contextlib import contextmanager

    from app.workflows import tasks as wf_tasks

    tenant = Tenant(
        id=uuid.uuid4(),
        name="Task Guard Tenant",
        slug=f"task-sec-{uuid.uuid4().hex[:8]}",
        region=Region.US,
    )
    set_rls_context(rolled_back_db, tenant_id=tenant.id)
    rolled_back_db.add(tenant)
    rolled_back_db.flush()

    for bad_status in (DepartmentStatus.INACTIVE, DepartmentStatus.ARCHIVED):
        dept = Department(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name=f"Dept {bad_status.value}",
            code=f"dept-{bad_status.value}",
            department_type=DepartmentType.SUPPORT,
            status=bad_status,
        )
        rolled_back_db.add(dept)
        rolled_back_db.flush()

        wf = Workflow(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            department_id=dept.id,
            name=f"WF {bad_status.value}",
        )
        rolled_back_db.add(wf)
        rolled_back_db.flush()

        ver = WorkflowVersion(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            workflow_id=wf.id,
            version_number=1,
            status=WorkflowVersionStatus.PUBLISHED,
            definition={"trigger": {"type": "manual"}, "nodes": [], "edges": []},
        )
        rolled_back_db.add(ver)
        rolled_back_db.flush()

        class _SessionWrapper:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

            @contextmanager
            def begin(self_inner):
                nested = rolled_back_db.begin_nested()
                try:
                    yield
                finally:
                    nested.rollback()

            def __getattr__(self_inner, item):
                return getattr(rolled_back_db, item)

        monkeypatch.setattr(wf_tasks, "get_session_factory", lambda: _SessionWrapper)

        with pytest.raises(ValueError, match="not active"):
            wf_tasks.execute_workflow_run_task.run(str(tenant.id), str(wf.id), {"x": 1})

        runs = rolled_back_db.scalars(select(Run).where(Run.workflow_id == wf.id)).all()
        assert runs == []
