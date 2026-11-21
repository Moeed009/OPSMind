"""Stage 2-4 tests: 5 approved departments, 6 approved workflows, 9 node types, Knowledge Hub scope & sync, Eval Gate, Cost Limits, Replay, Zoho Invoice, and HubSpot Tickets."""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ai.exceptions import AIProviderUnavailableError
from app.ai.providers.dept_providers import FakeProvider
from app.ai.services.dept_ai_service import AiContext, DepartmentAiService
from app.api.deps import get_current_user, get_user_db
from app.connectors import crypto
from app.connectors.http import HttpResponse
from app.connectors.zoho_books.connector import ZohoBooksConnector
from app.core.config import get_settings
from app.core.security import AuthUser
from app.db.enums import (
    ConnectionStatus,
    DepartmentType,
    DocumentSource,
    Region,
    Role,
    RunStatus,
    TriggerSource,
    WorkflowVersionStatus,
)
from app.db.models import (
    AIRun,
    Connection,
    Document,
    Membership,
    Run,
    Tenant,
    User,
    Workflow,
    WorkflowTemplate,
    WorkflowVersion,
)
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.main import app
from app.schemas.department import DepartmentCreate
from app.schemas.department_workflow import DeptWorkflowCreate
from app.services.department import create_department, list_templates
from app.services.department_workflows import (
    activate_department_workflow,
    create_department_workflow,
    run_workflow_evaluation,
    submit_workflow_for_review,
)
from app.services.knowledge_sync import sync_connector_to_knowledge_hub, upsert_knowledge_document
from app.workflows.action_registry import execute_registered_action, is_action_available
from app.workflows.definition import WorkflowDefinition
from app.workflows.dept_executor import execute_department_run
from app.workflows.template_seed import APPROVED_TEMPLATE_KEYS, seed_workflow_templates


class _FakeHttp:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs) -> HttpResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.handler(method, url, kwargs)


@pytest.fixture()
def db_session():
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


@pytest.fixture()
def admin_session():
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    conn = engine.connect()
    outer_tx = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer_tx.rollback()
        conn.close()
        engine.dispose()


def _encrypt(tenant_id: uuid.UUID, conn_id: uuid.UUID, payload: dict[str, Any]):
    s = get_settings()
    keyring = crypto.KeyRing.from_env(
        {
            "CONNECTOR_ENCRYPTION_KEYS": s.connector_encryption_keys,
            "CONNECTOR_ENCRYPTION_CURRENT_KEY_ID": s.connector_encryption_current_key_id,
        }
    )
    ct, kid = crypto.encrypt(payload, tenant_id=tenant_id, connection_id=conn_id, keyring=keyring)
    return ct.encode("ascii"), kid


def _seed_tenant(db: Session):
    owner = User(id=uuid.uuid4(), email=f"owner-{uuid.uuid4().hex[:6]}@opsmind.dev", full_name="Owner")
    viewer = User(id=uuid.uuid4(), email=f"viewer-{uuid.uuid4().hex[:6]}@opsmind.dev", full_name="Viewer")
    for u in (owner, viewer):
        set_rls_context(db, user_id=u.id)
        db.add(u)
        db.flush()

    tenant = Tenant(id=uuid.uuid4(), name="OpsMind 5-Dept Co", slug=f"five-{uuid.uuid4().hex[:6]}", region=Region.US)
    set_rls_context(db, tenant_id=tenant.id, user_id=owner.id)
    db.add(tenant)
    db.flush()

    db.add_all(
        [
            Membership(tenant_id=tenant.id, user_id=owner.id, role=Role.OWNER),
            Membership(tenant_id=tenant.id, user_id=viewer.id, role=Role.VIEWER),
        ]
    )
    db.flush()
    return owner, viewer, tenant


def test_five_approved_departments_and_six_templates_seeded(admin_session: Session):
    tpls = list_templates()
    keys = {t.key for t in tpls}
    assert keys == {"support", "finance", "sales", "legal", "operations"}
    assert "hr" not in keys
    assert "marketing" not in keys

    owner, _viewer, tenant = _seed_tenant(admin_session)
    legal = create_department(
        admin_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Legal", template_key="legal"),
    )
    assert legal.department_type == DepartmentType.LEGAL

    # Seed templates: deactivates any unapproved templates and activates only the 6 approved
    admin_session.add(
        WorkflowTemplate(
            key="legacy_hr_test_tpl",
            department_type="hr",
            name="Old HR",
            definition={"trigger": {"type": "manual"}, "nodes": [{"id": "w1", "type": "wait"}], "edges": []},
            required_connectors=[],
            is_active=True,
        )
    )
    admin_session.flush()

    seed_workflow_templates(admin_session)
    active_keys = {
        t.key
        for t in admin_session.scalars(select(WorkflowTemplate).where(WorkflowTemplate.is_active.is_(True))).all()
    }
    assert active_keys == APPROVED_TEMPLATE_KEYS
    old_hr = admin_session.scalar(select(WorkflowTemplate).where(WorkflowTemplate.key == "legacy_hr_test_tpl"))
    assert old_hr is not None and old_hr.is_active is False


def test_strict_eval_gate_and_knowledge_scope_and_policy_qa(db_session: Session):
    owner, _viewer, tenant = _seed_tenant(db_session)
    ops = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Internal Operations", template_key="operations"),
    )
    finance = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Finance", template_key="finance"),
    )

    # Company-shared policy vs Finance-private PO
    upsert_knowledge_document(
        db_session,
        tenant_id=tenant.id,
        department_id=None,
        scope="company_shared",
        title="Remote Work Policy",
        content="Employees receive a $500 annual home office stipend.",
        source=DocumentSource.UPLOAD,
    )
    upsert_knowledge_document(
        db_session,
        tenant_id=tenant.id,
        department_id=finance.id,
        scope="department_private",
        title="Finance Secret PO-999",
        content="PO-999 confidential vendor banking details.",
        source=DocumentSource.UPLOAD,
    )

    qa_tpl = db_session.scalar(select(WorkflowTemplate).where(WorkflowTemplate.key == "ops_policy_qa"))
    if qa_tpl is None:
        adm_engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        with Session(adm_engine) as adm:
            seed_workflow_templates(adm)
            adm.commit()
        adm_engine.dispose()
        qa_tpl = db_session.scalar(select(WorkflowTemplate).where(WorkflowTemplate.key == "ops_policy_qa"))
    assert qa_tpl is not None

    # Create Policy Q&A workflow (uses slack action, so connect Slack to Ops first for Eval Gate)
    slack_cid = uuid.uuid4()
    s_ct, s_kid = _encrypt(tenant.id, slack_cid, {"access_token": "xoxb-ops"})
    db_session.add(
        Connection(
            id=slack_cid,
            tenant_id=tenant.id,
            department_id=ops.id,
            provider="slack",
            display_name="Ops Slack",
            external_account_id="ops-slack",
            status=ConnectionStatus.HEALTHY,
            credentials_ciphertext=s_ct,
            credentials_key_id=s_kid,
        )
    )
    db_session.flush()

    wf_out = create_department_workflow(
        db_session,
        tenant_id=tenant.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        department_id=ops.id,
        payload=DeptWorkflowCreate(
            name="Policy Q&A",
            definition=WorkflowDefinition.model_validate(qa_tpl.definition),
            template_key="ops_policy_qa",
        ),
    )
    submit_workflow_for_review(
        db_session,
        tenant_id=tenant.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        department_id=ops.id,
        workflow_id=wf_out.id,
    )

    # Strict Eval Gate blocks publish before eval passes
    with pytest.raises(HTTPException) as exc_info:
        activate_department_workflow(
            db_session,
            tenant_id=tenant.id,
            user_id=owner.id,
            tenant_role=Role.OWNER,
            department_id=ops.id,
            workflow_id=wf_out.id,
        )
    assert exc_info.value.status_code == 409

    eval_out = run_workflow_evaluation(
        db_session,
        tenant_id=tenant.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        department_id=ops.id,
        workflow_id=wf_out.id,
    )
    assert eval_out["passed"] is True

    pub_out = activate_department_workflow(
        db_session,
        tenant_id=tenant.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        department_id=ops.id,
        workflow_id=wf_out.id,
    )
    assert pub_out.status == "active"

    # Execute Policy Q&A for unknown question (cannot read Finance Secret PO-999; returns I don't know & escalates)
    ver = db_session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == wf_out.id,
            WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
        )
    )
    run = Run(
        tenant_id=tenant.id,
        department_id=ops.id,
        workflow_id=wf_out.id,
        workflow_version_id=ver.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.EVENT,
        trace_id=uuid.uuid4().hex,
        input={"question": "What are the confidential vendor banking details for PO-999?"},
    )
    db_session.add(run)
    db_session.flush()

    fake_agent = FakeProvider(
        "groq",
        responses=[json.dumps({"text": "Trying to answer", "confidence": 0.9, "has_reliable_source": True, "sources": []})],
    )
    svc = DepartmentAiService(providers={"groq": fake_agent, "gemini": FakeProvider("gemini")})
    res_run = execute_department_run(db_session, run.id, ai_service=svc)
    assert res_run.status == RunStatus.WAITING_APPROVAL
    assert res_run.output["retrieve_1"]["match_count"] == 0
    assert "I don't know" in res_run.output["agent_1"]["text"]


def test_ai_token_cost_limit_and_wait_node_and_replay(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    owner, viewer, tenant = _seed_tenant(db_session)
    dept = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Internal Operations", template_key="operations"),
    )

    # Test Wait node + Replay endpoint
    wf = Workflow(tenant_id=tenant.id, department_id=dept.id, name="Wait Replay WF", created_by=owner.id)
    db_session.add(wf)
    db_session.flush()
    ver = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=wf.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={
            "trigger": {"type": "manual"},
            "nodes": [
                {"id": "wait_1", "type": "wait", "label": "Wait 5s", "wait_seconds": 5},
                {"id": "ret_1", "type": "retrieve", "label": "Ops Aggregate", "mode": "aggregate_ops"},
            ],
            "edges": [{"source": "wait_1", "target": "ret_1"}],
        },
        created_by=owner.id,
    )
    db_session.add(ver)
    db_session.flush()

    run = Run(
        tenant_id=tenant.id,
        department_id=dept.id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.EVENT,
        trace_id=uuid.uuid4().hex,
        input={"period": "weekly"},
    )
    db_session.add(run)
    db_session.flush()
    run = execute_department_run(db_session, run.id)
    assert run.status == RunStatus.SUCCEEDED
    assert run.output["wait_1"]["waited"] is True
    assert run.output["ret_1"]["mode"] == "aggregate_ops"

    # Replay via API (viewer blocked 403, owner succeeds 201)
    auth_state = {"user": viewer}

    def override_user():
        return AuthUser(id=auth_state["user"].id, email=auth_state["user"].email)

    def override_db():
        set_rls_context(db_session, user_id=auth_state["user"].id)
        yield db_session

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_user_db] = override_db
    try:
        client = TestClient(app)
        r403 = client.post(
            f"/departments/{dept.id}/runs/{run.id}/replay",
            headers={"X-Tenant-ID": str(tenant.id)},
        )
        assert r403.status_code == 403

        auth_state["user"] = owner
        r201 = client.post(
            f"/departments/{dept.id}/runs/{run.id}/replay",
            headers={"X-Tenant-ID": str(tenant.id)},
        )
        assert r201.status_code == 201
        assert r201.json()["id"] != str(run.id)
        assert r201.json()["status"] == "succeeded"
    finally:
        app.dependency_overrides.clear()

    # Token budget limit check
    db_session.add(
        AIRun(
            tenant_id=tenant.id,
            department_id=dept.id,
            node_id="n1",
            provider="groq",
            model="openai/gpt-oss-20b",
            task="classify",
            status="success",
            latency_ms=10,
            input_tokens=400_000,
            output_tokens=150_000,
        )
    )
    db_session.flush()
    svc = DepartmentAiService(providers={"groq": FakeProvider("groq"), "gemini": FakeProvider("gemini")})
    import asyncio

    with pytest.raises(AIProviderUnavailableError, match="token limit"):
        asyncio.run(
            svc.run(
                db_session,
                AiContext(tenant_id=tenant.id, department_id=dept.id),
                "classify",
                {"text": "hi"},
                {"id": "c1", "type": "classify"},
            )
        )


def test_zoho_invoice_hubspot_tickets_and_knowledge_sync(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    for key in (
        "hubspot.create_ticket",
        "hubspot.update_ticket",
        "hubspot.create_contact",
        "zoho_books.create_bill",
        "google_calendar.create_event",
        "google_drive.upload_file",
    ):
        assert is_action_available(key) is True

    owner, _viewer, tenant = _seed_tenant(db_session)
    support = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Customer Support", template_key="support"),
    )
    sales = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Sales", template_key="sales"),
    )
    finance = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Finance", template_key="finance"),
    )

    # Separate HubSpot connection for Sales ONLY -> Support CANNOT use Sales' HubSpot connection!
    sales_hs_id = uuid.uuid4()
    s_ct, s_kid = _encrypt(tenant.id, sales_hs_id, {"access_token": "hs-sales-tok"})
    db_session.add(
        Connection(
            id=sales_hs_id,
            tenant_id=tenant.id,
            department_id=sales.id,
            provider="hubspot",
            display_name="Sales HubSpot",
            external_account_id="sales-hs",
            status=ConnectionStatus.HEALTHY,
            credentials_ciphertext=s_ct,
            credentials_key_id=s_kid,
        )
    )
    zoho_id = uuid.uuid4()
    z_ct, z_kid = _encrypt(tenant.id, zoho_id, {"access_token": "zoho-fin-tok", "refresh_token": "1//z"})
    zoho_conn = Connection(
        id=zoho_id,
        tenant_id=tenant.id,
        department_id=finance.id,
        provider="zoho_books",
        display_name="Finance Zoho Books",
        external_account_id="org-1",
        status=ConnectionStatus.HEALTHY,
        credentials_ciphertext=z_ct,
        credentials_key_id=z_kid,
    )
    db_session.add(zoho_conn)
    db_session.flush()

    wf = Workflow(tenant_id=tenant.id, department_id=support.id, name="Sup WF", created_by=owner.id)
    db_session.add(wf)
    db_session.flush()
    ver = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=wf.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={"trigger": {"type": "manual"}, "nodes": [{"id": "w1", "type": "wait"}], "edges": []},
        created_by=owner.id,
    )
    db_session.add(ver)
    db_session.flush()
    run = Run(
        tenant_id=tenant.id,
        department_id=support.id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        status=RunStatus.RUNNING,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={},
    )
    db_session.add(run)
    db_session.flush()

    # Support blocked from Sales' HubSpot connection
    r_iso = execute_registered_action(
        db_session,
        tenant_id=tenant.id,
        department_id=support.id,
        run_id=run.id,
        run_step_id=None,
        action_key="hubspot.create_ticket",
        params={"subject": "Help", "idempotency_key": "hs-iso-1"},
        context={},
    )
    assert r_iso.success is False
    assert "Cross-department fallback is prohibited" in (r_iso.error or "")

    # Zoho Books create_bill via mocked HTTP
    def zoho_handler(method: str, url: str, kwargs: dict[str, Any]) -> HttpResponse:
        if method == "POST" and url.endswith("/bills"):
            return HttpResponse(200, {"bill": {"bill_id": "bill-901", "bill_number": "BILL-901", "status": "open", "total": 250}})
        return HttpResponse(404, {})

    monkeypatch.setattr(
        "app.workflows.actions._build_zoho_connector",
        lambda: ZohoBooksConnector(_FakeHttp(zoho_handler), "zid", "zsec"),
    )
    r_zoho = execute_registered_action(
        db_session,
        tenant_id=tenant.id,
        department_id=finance.id,
        run_id=run.id,
        run_step_id=None,
        action_key="zoho_books.create_bill",
        params={"bill_number": "BILL-901", "total_amount": 250, "idempotency_key": "zoho-1"},
        context={},
    )
    assert r_zoho.success is True
    assert r_zoho.external_ref == "bill-901"

    # Knowledge Hub Sync Service indexes records as department_private
    sync_out = sync_connector_to_knowledge_hub(
        db_session,
        tenant_id=tenant.id,
        connection_id=zoho_conn.id,
        actor_user_id=owner.id,
        record_fetcher=lambda _c: [
            {"external_id": "po:1", "title": "PO-101", "content": "Purchase Order PO-101 for Acme Supplies $250"}
        ],
    )
    assert sync_out["synced_count"] == 1
    assert sync_out["scope"] == "department_private"
    synced_doc = db_session.scalar(
        select(Document).where(Document.tenant_id == tenant.id, Document.external_id == "po:1")
    )
    assert synced_doc is not None and synced_doc.department_id == finance.id


def test_replay_idempotency_pii_masking_rbac_and_approval_paths(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.run_step import RunStep
    from app.db.models.workflow_approval import WorkflowApproval
    from app.workflows.dept_executor import decide_workflow_approval, replay_department_run

    owner, viewer, tenant = _seed_tenant(db_session)
    finance = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Finance", template_key="finance"),
    )

    # 1. PII masking before AI call
    import asyncio

    cap_provider = FakeProvider("groq", responses=[json.dumps({"label": "invoice", "confidence": 0.95})])
    svc = DepartmentAiService(providers={"groq": cap_provider, "gemini": FakeProvider("gemini")})
    asyncio.run(
        svc.run(
            db_session,
            AiContext(tenant_id=tenant.id, department_id=finance.id),
            "classify",
            {"body": "Contact alice@example.com or +1-555-019-9999 about INV-42"},
            {"id": "c1", "type": "classify", "labels": ["invoice", "other"]},
        )
    )
    assert cap_provider.calls
    assert "alice@example.com" not in cap_provider.calls[0]["user_content"]

    # 2. Replay idempotency: side-effect action executes ONCE across original + replay
    zoho_id = uuid.uuid4()
    z_ct, z_kid = _encrypt(tenant.id, zoho_id, {"access_token": "zoho-fin-tok"})
    db_session.add(
        Connection(
            id=zoho_id,
            tenant_id=tenant.id,
            department_id=finance.id,
            provider="zoho_books",
            display_name="Finance Zoho Books",
            external_account_id="org-replay",
            status=ConnectionStatus.HEALTHY,
            credentials_ciphertext=z_ct,
            credentials_key_id=z_kid,
        )
    )
    db_session.flush()

    bill_calls = 0

    def replay_handler(method: str, url: str, kwargs: dict[str, Any]) -> HttpResponse:
        nonlocal bill_calls
        if method == "POST" and url.endswith("/bills"):
            bill_calls += 1
            return HttpResponse(200, {"bill": {"bill_id": "bill-replay-1", "bill_number": "B-1"}})
        return HttpResponse(404, {})

    monkeypatch.setattr(
        "app.workflows.actions._build_zoho_connector",
        lambda: ZohoBooksConnector(_FakeHttp(replay_handler), "zid", "zsec"),
    )

    wf = Workflow(tenant_id=tenant.id, department_id=finance.id, name="Replay & Approval WF", created_by=owner.id)
    db_session.add(wf)
    db_session.flush()
    ver = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=wf.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={
            "trigger": {"type": "manual"},
            "nodes": [
                {"id": "cond_1", "type": "condition", "expression": "trigger.amount >= 100"},
                {"id": "app_1", "type": "approval", "role": "reviewer", "timeout_hours": 24},
                {"id": "act_true", "type": "action", "action": "zoho_books.create_bill", "params": {"bill_number": "B-1", "total_amount": 150}},
                {"id": "act_false", "type": "wait", "wait_seconds": 1},
            ],
            "edges": [
                {"source": "cond_1", "target": "app_1", "label": "true"},
                {"source": "cond_1", "target": "act_false", "label": "false"},
                {"source": "app_1", "target": "act_true"},
            ],
        },
        created_by=owner.id,
    )
    db_session.add(ver)
    db_session.flush()

    # Conditional branch TRUE -> pauses at Human Approval
    run1 = Run(
        tenant_id=tenant.id,
        department_id=finance.id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={"amount": 150},
    )
    db_session.add(run1)
    db_session.flush()
    r1 = execute_department_run(db_session, run1.id, ai_service=svc)
    assert r1.status == RunStatus.WAITING_APPROVAL
    approval1 = db_session.scalar(select(WorkflowApproval).where(WorkflowApproval.run_id == r1.id))
    assert approval1 is not None

    # Unauthorized role (viewer) blocked from approving
    with pytest.raises(HTTPException) as exc_rbac:
        decide_workflow_approval(
            db_session,
            tenant_id=tenant.id,
            department_id=finance.id,
            approval_id=approval1.id,
            user_id=viewer.id,
            tenant_role=Role.VIEWER,
            decision="approve",
        )
    assert exc_rbac.value.status_code == 403

    # Owner approves -> zoho_books.create_bill executes ONCE
    decide_workflow_approval(
        db_session,
        tenant_id=tenant.id,
        department_id=finance.id,
        approval_id=approval1.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        decision="approve",
    )
    assert bill_calls == 1

    # Conditional branch FALSE -> skips approval & bill, runs wait_1
    run_false = Run(
        tenant_id=tenant.id,
        department_id=finance.id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={"amount": 20},
    )
    db_session.add(run_false)
    db_session.flush()
    r_false = execute_department_run(db_session, run_false.id, ai_service=svc)
    assert r_false.status == RunStatus.SUCCEEDED
    assert bill_calls == 1

    # Reject path
    run_rej = Run(
        tenant_id=tenant.id,
        department_id=finance.id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={"amount": 200},
    )
    db_session.add(run_rej)
    db_session.flush()
    execute_department_run(db_session, run_rej.id, ai_service=svc)
    approval_rej = db_session.scalar(select(WorkflowApproval).where(WorkflowApproval.run_id == run_rej.id))
    assert approval_rej is not None
    decide_workflow_approval(
        db_session,
        tenant_id=tenant.id,
        department_id=finance.id,
        approval_id=approval_rej.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        decision="reject",
        comment="Rejected",
    )
    db_session.refresh(run_rej)
    assert run_rej.status == RunStatus.CANCELLED

    # Execution history (RunStep) persisted
    steps = db_session.scalars(select(RunStep).where(RunStep.run_id == r1.id)).all()
    assert len(steps) >= 3

    # Direct action-only workflow for clean replay idempotency verification
    wf_act = Workflow(tenant_id=tenant.id, department_id=finance.id, name="Direct Action Replay", created_by=owner.id)
    db_session.add(wf_act)
    db_session.flush()
    ver_act = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=wf_act.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={
            "trigger": {"type": "manual"},
            "nodes": [{"id": "act_1", "type": "action", "action": "zoho_books.create_bill", "params": {"bill_number": "B-2", "total_amount": 99}}],
            "edges": [],
        },
        created_by=owner.id,
    )
    db_session.add(ver_act)
    db_session.flush()
    r_orig = Run(
        tenant_id=tenant.id,
        department_id=finance.id,
        workflow_id=wf_act.id,
        workflow_version_id=ver_act.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={},
    )
    db_session.add(r_orig)
    db_session.flush()
    execute_department_run(db_session, r_orig.id, ai_service=svc)
    assert bill_calls == 2
    r_rep = replay_department_run(
        db_session,
        tenant_id=tenant.id,
        department_id=finance.id,
        run_id=r_orig.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        ai_service=svc,
    )
    assert r_rep.status == RunStatus.SUCCEEDED
    # Replay MUST reuse ActionLog idempotency key -> bill_calls remains 2!
    assert bill_calls == 2


def test_stripe_money_moving_requires_approval_and_blocks_agent_direct_trigger(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.workflow_approval import WorkflowApproval
    from app.workflows.dept_executor import decide_workflow_approval

    owner, _viewer, tenant = _seed_tenant(db_session)
    finance = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Finance", template_key="finance"),
    )
    stripe_cid = uuid.uuid4()
    st_ct, st_kid = _encrypt(tenant.id, stripe_cid, {"access_token": "sk_test_123"})
    db_session.add(
        Connection(
            id=stripe_cid,
            tenant_id=tenant.id,
            department_id=finance.id,
            provider="stripe",
            display_name="Finance Stripe",
            external_account_id="acct_fin",
            status=ConnectionStatus.HEALTHY,
            credentials_ciphertext=st_ct,
            credentials_key_id=st_kid,
        )
    )
    db_session.flush()

    stripe_calls: list[str] = []

    class _FakeResp:
        status_code = 200
        content = b'{"id": "re_123", "status": "succeeded"}'
        text = '{"id": "re_123", "status": "succeeded"}'

        @staticmethod
        def json():
            return {"id": "re_123", "status": "succeeded"}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kw):
            stripe_calls.append(url)
            return _FakeResp()

    monkeypatch.setattr("app.workflows.actions.httpx.Client", _FakeClient)

    svc = DepartmentAiService(
        providers={
            "groq": FakeProvider("groq", responses=[json.dumps({"text": "Refund approved by AI", "confidence": 0.99})]),
            "gemini": FakeProvider("gemini"),
        }
    )

    # 1. Agent -> stripe.create_refund WITHOUT human approval MUST BE BLOCKED
    wf_ungated = Workflow(tenant_id=tenant.id, department_id=finance.id, name="Agent Direct Refund", created_by=owner.id)
    db_session.add(wf_ungated)
    db_session.flush()
    ver_ungated = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=wf_ungated.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={
            "trigger": {"type": "manual"},
            "nodes": [
                {"id": "agent_1", "type": "agent", "prompt": "Check refund"},
                {"id": "refund_1", "type": "action", "action": "stripe.create_refund", "params": {"charge": "ch_1", "amount": 5000}},
            ],
            "edges": [{"source": "agent_1", "target": "refund_1"}],
        },
        created_by=owner.id,
    )
    db_session.add(ver_ungated)
    db_session.flush()
    r_ungated = Run(
        tenant_id=tenant.id,
        department_id=finance.id,
        workflow_id=wf_ungated.id,
        workflow_version_id=ver_ungated.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={"text": "Refund $50"},
    )
    db_session.add(r_ungated)
    db_session.flush()
    res_ungated = execute_department_run(db_session, r_ungated.id, ai_service=svc)
    assert res_ungated.status == RunStatus.FAILED
    assert "requires an approved ReviewTask or WorkflowApproval" in (res_ungated.error or "")
    assert len(stripe_calls) == 0

    # 2. Agent -> Human Approval (approved) -> stripe.create_refund SUCCEEDS
    wf_gated = Workflow(tenant_id=tenant.id, department_id=finance.id, name="Approved Refund", created_by=owner.id)
    db_session.add(wf_gated)
    db_session.flush()
    ver_gated = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=wf_gated.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={
            "trigger": {"type": "manual"},
            "nodes": [
                {"id": "agent_1", "type": "agent", "prompt": "Draft refund"},
                {"id": "app_1", "type": "approval", "role": "admin", "timeout_hours": 24},
                {"id": "refund_1", "type": "action", "action": "stripe.create_refund", "params": {"charge": "ch_1", "amount": 5000}},
            ],
            "edges": [
                {"source": "agent_1", "target": "app_1"},
                {"source": "app_1", "target": "refund_1"},
            ],
        },
        created_by=owner.id,
    )
    db_session.add(ver_gated)
    db_session.flush()
    r_gated = Run(
        tenant_id=tenant.id,
        department_id=finance.id,
        workflow_id=wf_gated.id,
        workflow_version_id=ver_gated.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={"text": "Refund $50"},
    )
    db_session.add(r_gated)
    db_session.flush()
    execute_department_run(db_session, r_gated.id, ai_service=svc)
    app_row = db_session.scalar(select(WorkflowApproval).where(WorkflowApproval.run_id == r_gated.id))
    assert app_row is not None
    decide_workflow_approval(
        db_session,
        tenant_id=tenant.id,
        department_id=finance.id,
        approval_id=app_row.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        decision="approve",
    )
    db_session.refresh(r_gated)
    assert r_gated.status == RunStatus.SUCCEEDED
    assert len(stripe_calls) == 1


def test_archived_former_hr_dept_excluded_from_active_listings_and_weekly_report(db_session: Session):
    from app.db.enums import DepartmentStatus
    from app.db.models.department import Department
    from app.services.department import list_departments

    owner, _viewer, tenant = _seed_tenant(db_session)
    active_ops = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Internal Operations", template_key="operations"),
    )
    archived_hr = Department(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Human Resources (archived-849a09)",
        code="arch_hr_849a",
        department_type=DepartmentType.OPERATIONS,
        status=DepartmentStatus.ARCHIVED,
        created_by=owner.id,
    )
    db_session.add(archived_hr)
    db_session.flush()

    # 1. Active listing (default include_archived=False) shows ONLY 1 Operations department
    active_list = list_departments(
        db_session,
        tenant_id=tenant.id,
        user_id=owner.id,
        role=Role.OWNER,
        include_archived=False,
    )
    ops_depts = [d for d in active_list if d.department_type == DepartmentType.OPERATIONS]
    assert len(active_list) == 1
    assert len(ops_depts) == 1
    assert ops_depts[0].id == active_ops.id

    # 2. Weekly Report aggregate_ops excludes runs from the archived former-HR department
    wf_ops = Workflow(tenant_id=tenant.id, department_id=active_ops.id, name="Ops Report", created_by=owner.id)
    wf_arch = Workflow(tenant_id=tenant.id, department_id=archived_hr.id, name="Old HR WF", created_by=owner.id)
    db_session.add_all([wf_ops, wf_arch])
    db_session.flush()

    ver_ops = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=wf_ops.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={
            "trigger": {"type": "manual"},
            "nodes": [{"id": "ret_1", "type": "retrieve", "mode": "aggregate_ops"}],
            "edges": [],
        },
        created_by=owner.id,
    )
    ver_arch = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=wf_arch.id,
        version_number=1,
        status=WorkflowVersionStatus.PAUSED,
        definition={"trigger": {"type": "manual"}, "nodes": [{"id": "w1", "type": "wait"}], "edges": []},
        created_by=owner.id,
    )
    db_session.add_all([ver_ops, ver_arch])
    db_session.flush()

    db_session.add(
        Run(
            tenant_id=tenant.id,
            department_id=archived_hr.id,
            workflow_id=wf_arch.id,
            workflow_version_id=ver_arch.id,
            status=RunStatus.SUCCEEDED,
            trigger_source=TriggerSource.MANUAL,
            trace_id=uuid.uuid4().hex,
            input={},
        )
    )
    r_report = Run(
        tenant_id=tenant.id,
        department_id=active_ops.id,
        workflow_id=wf_ops.id,
        workflow_version_id=ver_ops.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={},
    )
    db_session.add(r_report)
    db_session.flush()

    finished = execute_department_run(db_session, r_report.id)
    assert finished.status == RunStatus.SUCCEEDED
    agg_depts = finished.output["ret_1"]["departments"]
    assert "Internal Operations" in agg_depts
    assert "Human Resources (archived-849a09)" not in agg_depts
    assert len(agg_depts) == 1


def test_legacy_history_readable_but_execution_replay_and_approval_blocked(db_session: Session):
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from app.api.deps import TenantContext
    from app.api.v1.reviews import decide_review_task, list_review_tasks
    from app.api.v1.runs import get_run_details, list_runs, replay_run_endpoint, trigger_workflow_run
    from app.db.enums import ActionStatus, ReviewTaskStatus, RunStepStatus
    from app.db.models.action_log import ActionLog
    from app.db.models.review_task import ReviewTask
    from app.db.models.run_step import RunStep
    from app.schemas.review import ReviewTaskDecisionRequest
    from app.schemas.run import RunTriggerRequest

    owner, _viewer, tenant = _seed_tenant(db_session)

    # Create a legacy (department_id=None) workflow, version, run, step, review_task, and action_log
    legacy_wf = Workflow(
        tenant_id=tenant.id,
        department_id=None,
        name="Legacy Company Triage",
        created_by=owner.id,
    )
    db_session.add(legacy_wf)
    db_session.flush()
    legacy_ver = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=legacy_wf.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={"nodes": [], "edges": []},
        created_by=owner.id,
    )
    db_session.add(legacy_ver)
    db_session.flush()
    legacy_run = Run(
        tenant_id=tenant.id,
        department_id=None,
        workflow_id=legacy_wf.id,
        workflow_version_id=legacy_ver.id,
        status=RunStatus.WAITING_APPROVAL,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={"subject": "Legacy ticket"},
    )
    db_session.add(legacy_run)
    db_session.flush()
    legacy_step = RunStep(
        tenant_id=tenant.id,
        run_id=legacy_run.id,
        step_index=0,
        node_id="app_1",
        step_type="approval",
        status=RunStepStatus.COMPLETED,
        input={},
        output={"status": "waiting"},
    )
    db_session.add(legacy_step)
    db_session.flush()
    legacy_rt = ReviewTask(
        tenant_id=tenant.id,
        run_id=legacy_run.id,
        run_step_id=legacy_step.id,
        status=ReviewTaskStatus.OPEN,
        risk_reason="Legacy approval",
        proposal={"draft": "Hello"},
        evidence={"source": "legacy"},
        confidence=Decimal("0.90"),
        deadline_at=datetime.now(UTC) + timedelta(hours=24),
    )
    legacy_al = ActionLog(
        tenant_id=tenant.id,
        run_id=legacy_run.id,
        run_step_id=legacy_step.id,
        connection_id=None,
        action_type="slack.post_message",
        idempotency_key=f"legacy_idem_{uuid.uuid4().hex}",
        status=ActionStatus.SUCCEEDED,
        request={"channel": "#general"},
        response={"ok": True},
        attempts=1,
    )
    db_session.add_all([legacy_rt, legacy_al])
    db_session.flush()

    tc = TenantContext(user=owner, tenant_id=tenant.id, role=Role.OWNER, db=db_session)

    # 1. Legacy run, steps, review_tasks, and action_logs remain readable read-only
    runs_list = list_runs(tc=tc, workflow_id=None, department_id=None, limit=25)
    assert any(r.id == legacy_run.id and r.department_id is None for r in runs_list)
    run_detail = get_run_details(run_id=legacy_run.id, tc=tc)
    assert run_detail.id == legacy_run.id
    assert len(run_detail.steps) == 1

    reviews_list = list_review_tasks(tc=tc, status_filter="open", status=None)
    matched_rt = next((t for t in reviews_list if t.id == legacy_rt.id), None)
    assert matched_rt is not None
    assert matched_rt.department_id is None
    assert matched_rt.is_legacy_readonly is True

    # 2. Executing a legacy workflow is blocked with 409 Conflict
    with pytest.raises(HTTPException) as exc_exec:
        trigger_workflow_run(workflow_id=legacy_wf.id, payload=RunTriggerRequest(input_data={}), tc=tc)
    assert exc_exec.value.status_code == 409
    assert "read-only" in str(exc_exec.value.detail).lower()

    # 3. Replaying a legacy run is blocked with 409 Conflict
    with pytest.raises(HTTPException) as exc_rep:
        replay_run_endpoint(run_id=legacy_run.id, tc=tc)
    assert exc_rep.value.status_code == 409
    assert "read-only" in str(exc_rep.value.detail).lower()

    # 4. Approving/deciding a legacy review_task is blocked with 409 Conflict
    with pytest.raises(HTTPException) as exc_dec:
        decide_review_task(
            task_id=legacy_rt.id,
            payload=ReviewTaskDecisionRequest(decision="approved", reason="Try approve legacy"),
            tc=tc,
        )
    assert exc_dec.value.status_code == 409
    assert "read-only" in str(exc_dec.value.detail).lower()

    # 5. list_all_department_workflows returns active department workflows and excludes legacy workflows
    from app.services.department_workflows import list_all_department_workflows

    active_dept = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Support", template_key="support"),
    )
    dept_wf = Workflow(
        tenant_id=tenant.id,
        department_id=active_dept.id,
        name="Support Ticket Triage",
        created_by=owner.id,
    )
    db_session.add(dept_wf)
    db_session.flush()
    db_session.add(
        WorkflowVersion(
            tenant_id=tenant.id,
            workflow_id=dept_wf.id,
            version_number=1,
            status=WorkflowVersionStatus.PUBLISHED,
            definition={"trigger": {"type": "manual"}, "nodes": [], "edges": []},
            created_by=owner.id,
        )
    )
    db_session.flush()
    all_dept_wfs = list_all_department_workflows(
        db_session,
        tenant_id=tenant.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
    )
    assert [w.id for w in all_dept_wfs] == [dept_wf.id]
    assert all(w.department_id == active_dept.id for w in all_dept_wfs)

    # 6. Active aggregates (exclude_legacy=True, aggregate_ops Weekly Report, and department dashboard)
    #    exclude legacy runs/review_tasks with department_id IS NULL while read-only views keep them
    from app.services.department import get_department_dashboard

    dept_ver = db_session.scalar(
        select(WorkflowVersion).where(WorkflowVersion.workflow_id == dept_wf.id)
    )
    assert dept_ver is not None
    dept_run = Run(
        tenant_id=tenant.id,
        department_id=active_dept.id,
        workflow_id=dept_wf.id,
        workflow_version_id=dept_ver.id,
        status=RunStatus.SUCCEEDED,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={"subject": "Active department ticket"},
    )
    db_session.add(dept_run)
    db_session.flush()

    active_runs = list_runs(
        tc=tc, workflow_id=None, department_id=None, exclude_legacy=True, limit=25
    )
    assert [r.id for r in active_runs] == [dept_run.id]
    assert all(r.department_id is not None for r in active_runs)

    active_reviews = list_review_tasks(
        tc=tc, status_filter="open", status=None, exclude_legacy=True
    )
    assert all(r.id != legacy_rt.id and r.department_id is not None for r in active_reviews)

    dash = get_department_dashboard(
        db_session,
        tenant_id=tenant.id,
        user_id=owner.id,
        role=Role.OWNER,
        department_id=active_dept.id,
    )
    assert dash.metrics.total_runs == 1
    assert dash.metrics.succeeded_runs == 1

    # Weekly Report aggregate_ops excludes legacy_run (department_id=None)
    ops_wf = Workflow(
        tenant_id=tenant.id,
        department_id=active_dept.id,
        name="Weekly Ops Aggregate",
        created_by=owner.id,
    )
    db_session.add(ops_wf)
    db_session.flush()
    ops_ver = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=ops_wf.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={
            "trigger": {"type": "manual"},
            "nodes": [{"id": "ret_ops", "type": "retrieve", "mode": "aggregate_ops"}],
            "edges": [],
        },
        created_by=owner.id,
    )
    db_session.add(ops_ver)
    db_session.flush()
    ops_run = Run(
        tenant_id=tenant.id,
        department_id=active_dept.id,
        workflow_id=ops_wf.id,
        workflow_version_id=ops_ver.id,
        status=RunStatus.QUEUED,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={},
    )
    db_session.add(ops_run)
    db_session.flush()
    finished_ops = execute_department_run(db_session, ops_run.id)
    assert finished_ops.status == RunStatus.SUCCEEDED
    depts_summary = finished_ops.output["ret_ops"]["departments"]
    assert list(depts_summary.keys()) == ["Support"]
    # Only dept_run (SUCCEEDED) and ops_run (RUNNING during retrieve) are counted; legacy_run is excluded
    assert "WAITING_APPROVAL" not in depts_summary["Support"]["by_status"]
    assert "waiting_approval" not in depts_summary["Support"]["by_status"]
