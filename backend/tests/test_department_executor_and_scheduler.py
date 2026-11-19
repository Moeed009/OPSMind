"""Phase 4 tests: DAG executor, condition branching, approval approve/reject/expire, needs_review, scheduling, DST, event triggers, and tenant isolation."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers.dept_providers import FakeProvider
from app.ai.services.dept_ai_service import DepartmentAiService
from app.db.enums import (
    ConnectionStatus,
    DepartmentRole,
    Region,
    Role,
    RunStatus,
    TriggerSource,
    WorkflowApprovalStatus,
    WorkflowVersionStatus,
)
from app.db.models import Connection, Membership, Run, RunStep, Tenant, User, WorkflowApproval
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.schemas.department import DepartmentCreate
from app.schemas.department_workflow import DeptWorkflowCreate
from app.services.department import assign_members_to_department, create_department
from app.services.department_workflows import (
    activate_department_workflow,
    create_department_workflow,
    get_department_run_detail,
    pause_department_workflow,
    run_workflow_evaluation,
    submit_workflow_for_review,
)
from app.workflows.actions import ActionResult
from app.workflows.definition import ScheduleTrigger, WorkflowDefinition
from app.workflows.dept_executor import (
    decide_workflow_approval,
    dispatch_department_event_triggers,
    execute_department_run,
    expire_pending_approvals,
)
from app.workflows.scheduler import compute_due_slots, enqueue_due_scheduled_runs


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


def _seed(db: Session, *, timezone: str = "Asia/Karachi"):
    owner = User(id=uuid.uuid4(), email=f"owner-{uuid.uuid4().hex[:6]}@acme.com")
    reviewer = User(id=uuid.uuid4(), email=f"rev-{uuid.uuid4().hex[:6]}@acme.com")
    other_owner = User(id=uuid.uuid4(), email=f"other-{uuid.uuid4().hex[:6]}@other.com")
    for u in (owner, reviewer, other_owner):
        set_rls_context(db, user_id=u.id)
        db.add(u)
        db.flush()

    t1 = Tenant(id=uuid.uuid4(), name=f"Acme-{uuid.uuid4().hex[:6]}", slug=f"acme-{uuid.uuid4().hex[:6]}", region=Region.US)
    set_rls_context(db, user_id=owner.id, tenant_id=t1.id)
    db.add(t1)
    db.flush()
    db.add_all([
        Membership(tenant_id=t1.id, user_id=owner.id, role=Role.OWNER),
        Membership(tenant_id=t1.id, user_id=reviewer.id, role=Role.VIEWER),
    ])
    db.flush()

    t2 = Tenant(id=uuid.uuid4(), name=f"Other-{uuid.uuid4().hex[:6]}", slug=f"other-{uuid.uuid4().hex[:6]}", region=Region.US)
    set_rls_context(db, user_id=other_owner.id, tenant_id=t2.id)
    db.add(t2)
    db.flush()
    db.add(Membership(tenant_id=t2.id, user_id=other_owner.id, role=Role.OWNER))
    db.flush()

    set_rls_context(db, user_id=owner.id, tenant_id=t1.id)
    dept = create_department(
        db,
        tenant_id=t1.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Support", timezone=timezone),
    )
    assign_members_to_department(
        db,
        tenant_id=t1.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        department_id=dept.id,
        user_ids=[reviewer.id],
        role=DepartmentRole.REVIEWER,
    )
    conn = Connection(
        tenant_id=t1.id,
        department_id=dept.id,
        provider="gmail",
        display_name="Support Gmail",
        external_account_id="support@acme.com",
        status=ConnectionStatus.HEALTHY,
    )
    db.add(conn)
    db.flush()
    return {"t1": t1, "t2": t2, "dept": dept, "owner": owner, "reviewer": reviewer, "other_owner": other_owner, "conn": conn}


def test_executor_happy_path_and_condition_branching(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    env = _seed(db_session)
    t1, dept, owner = env["t1"], env["dept"], env["owner"]

    dispatched: list[dict] = []

    def fake_dispatch(**kwargs):
        dispatched.append(kwargs["config"])
        return ActionResult(success=True, action_type=kwargs["action_type"], external_ref="msg_1")

    monkeypatch.setattr("app.workflows.action_registry.ActionDispatcher.dispatch", staticmethod(fake_dispatch))

    defn = WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "manual"},
            "nodes": [
                {"id": "ai_1", "type": "ai", "task": "score"},
                {"id": "cond_1", "type": "condition", "expression": "ai_1.score >= 80"},
                {"id": "act_true", "type": "action", "action": "gmail.send_email", "params": {"body": "Score {{ai_1.score}}"}},
                {"id": "act_false", "type": "action", "action": "gmail.send_email", "params": {"body": "Low"}},
            ],
            "edges": [
                {"source": "ai_1", "target": "cond_1"},
                {"source": "cond_1", "target": "act_true", "label": "true"},
                {"source": "cond_1", "target": "act_false", "label": "false"},
            ],
        }
    )
    wf = create_department_workflow(
        db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id,
        payload=DeptWorkflowCreate(name="Branch Flow", definition=defn),
    )
    submit_workflow_for_review(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)
    assert run_workflow_evaluation(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)["passed"] is True
    activate_department_workflow(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)

    ver_id = db_session.scalar(
        select(Run).where(Run.id == uuid.uuid4())  # dummy check replaced below
    )
    del ver_id
    from app.db.models import WorkflowVersion
    ver = db_session.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id, WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED))
    assert ver is not None

    run = Run(
        tenant_id=t1.id, department_id=dept.id, workflow_id=wf.id, workflow_version_id=ver.id,
        trigger_source=TriggerSource.MANUAL, status=RunStatus.QUEUED, trace_id=uuid.uuid4().hex, input={"lead": "Acme"},
    )
    db_session.add(run)
    db_session.flush()

    ai_svc = DepartmentAiService(providers={"groq": FakeProvider("groq", responses=[json.dumps({"score": 92, "confidence": 0.95, "reasoning": "Hot"})])})
    finished = execute_department_run(db_session, run.id, ai_service=ai_svc)
    assert finished.status == RunStatus.SUCCEEDED
    assert len(dispatched) == 1
    assert dispatched[0]["body"] == "Score 92.0" or dispatched[0]["body"] == "Score 92"


def test_approval_approve_reject_expire_and_needs_review(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    env = _seed(db_session)
    t1, t2, dept, owner, reviewer, other_owner = env["t1"], env["t2"], env["dept"], env["owner"], env["reviewer"], env["other_owner"]

    monkeypatch.setattr(
        "app.workflows.action_registry.ActionDispatcher.dispatch",
        staticmethod(lambda **kwargs: ActionResult(success=True, action_type=kwargs["action_type"], external_ref="msg_ok")),
    )

    defn = WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "manual"},
            "nodes": [
                {"id": "ai_1", "type": "ai", "task": "draft_reply", "confidence_threshold": 0.5},
                {"id": "app_1", "type": "approval", "role": "reviewer", "timeout_hours": 24},
                {"id": "act_1", "type": "action", "action": "gmail.send_reply", "params": {"body": "{{ai_1.text}}"}},
            ],
            "edges": [
                {"source": "ai_1", "target": "app_1"},
                {"source": "app_1", "target": "act_1"},
            ],
        }
    )
    wf = create_department_workflow(
        db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id,
        payload=DeptWorkflowCreate(name="Approval Flow", definition=defn),
    )
    submit_workflow_for_review(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)
    assert run_workflow_evaluation(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)["passed"] is True
    activate_department_workflow(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)

    from app.db.models import WorkflowVersion
    ver = db_session.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id, WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED))
    assert ver is not None

    # 1. Approve flow
    r1 = Run(tenant_id=t1.id, department_id=dept.id, workflow_id=wf.id, workflow_version_id=ver.id, trigger_source=TriggerSource.MANUAL, status=RunStatus.QUEUED, trace_id=uuid.uuid4().hex, input={"msg": "hi"})
    db_session.add(r1)
    db_session.flush()
    ai_ok = DepartmentAiService(providers={"groq": FakeProvider("groq", responses=[json.dumps({"text": "Hello!", "confidence": 0.9})])})
    execute_department_run(db_session, r1.id, ai_service=ai_ok)
    assert r1.status == RunStatus.WAITING_APPROVAL
    app1 = db_session.scalar(select(WorkflowApproval).where(WorkflowApproval.run_id == r1.id))
    assert app1 is not None and app1.status == WorkflowApprovalStatus.PENDING

    decide_workflow_approval(db_session, tenant_id=t1.id, user_id=reviewer.id, tenant_role=Role.VIEWER, department_id=dept.id, approval_id=app1.id, decision="approve", comment="ok", ai_service=ai_ok)
    assert r1.status == RunStatus.SUCCEEDED
    assert app1.status == WorkflowApprovalStatus.APPROVED

    # Tenant isolation on run detail
    set_rls_context(db_session, user_id=other_owner.id, tenant_id=t2.id)
    with pytest.raises(HTTPException) as exc_iso:
        get_department_run_detail(db_session, tenant_id=t2.id, user_id=other_owner.id, tenant_role=Role.OWNER, department_id=dept.id, run_id=r1.id)
    assert exc_iso.value.status_code == 404
    set_rls_context(db_session, user_id=owner.id, tenant_id=t1.id)

    # 2. Reject flow
    r2 = Run(tenant_id=t1.id, department_id=dept.id, workflow_id=wf.id, workflow_version_id=ver.id, trigger_source=TriggerSource.MANUAL, status=RunStatus.QUEUED, trace_id=uuid.uuid4().hex, input={"msg": "hi"})
    db_session.add(r2)
    db_session.flush()
    ai_ok2 = DepartmentAiService(providers={"groq": FakeProvider("groq", responses=[json.dumps({"text": "Hello!", "confidence": 0.9})])})
    execute_department_run(db_session, r2.id, ai_service=ai_ok2)
    app2 = db_session.scalar(select(WorkflowApproval).where(WorkflowApproval.run_id == r2.id))
    assert app2 is not None
    decide_workflow_approval(db_session, tenant_id=t1.id, user_id=reviewer.id, tenant_role=Role.VIEWER, department_id=dept.id, approval_id=app2.id, decision="reject", comment="nope")
    assert r2.status == RunStatus.CANCELLED
    assert app2.status == WorkflowApprovalStatus.REJECTED

    # 3. Expire flow
    r3 = Run(tenant_id=t1.id, department_id=dept.id, workflow_id=wf.id, workflow_version_id=ver.id, trigger_source=TriggerSource.MANUAL, status=RunStatus.QUEUED, trace_id=uuid.uuid4().hex, input={"msg": "hi"})
    db_session.add(r3)
    db_session.flush()
    ai_ok3 = DepartmentAiService(providers={"groq": FakeProvider("groq", responses=[json.dumps({"text": "Hello!", "confidence": 0.9})])})
    execute_department_run(db_session, r3.id, ai_service=ai_ok3)
    app3 = db_session.scalar(select(WorkflowApproval).where(WorkflowApproval.run_id == r3.id))
    assert app3 is not None
    app3.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()
    expired_n = expire_pending_approvals(db_session)
    assert expired_n >= 1
    assert app3.status == WorkflowApprovalStatus.EXPIRED
    assert r3.status == RunStatus.CANCELLED

    # 4. needs_review from low-confidence AI
    r4 = Run(tenant_id=t1.id, department_id=dept.id, workflow_id=wf.id, workflow_version_id=ver.id, trigger_source=TriggerSource.MANUAL, status=RunStatus.QUEUED, trace_id=uuid.uuid4().hex, input={"msg": "hi"})
    db_session.add(r4)
    db_session.flush()
    ai_low = DepartmentAiService(providers={"groq": FakeProvider("groq", responses=[json.dumps({"text": "Unsure", "confidence": 0.2})])})
    execute_department_run(db_session, r4.id, ai_service=ai_low)
    assert r4.status == RunStatus.NEEDS_REVIEW
    steps = db_session.scalars(select(RunStep).where(RunStep.run_id == r4.id)).all()
    assert len(steps) == 1


def test_scheduling_idempotency_timezone_dst_and_disconnect_stops_triggers(db_session: Session):
    # DST test in America/New_York (2026-11-01 06:00 UTC == 01:00 EST after fallback, 2026-03-08 07:00 UTC == 03:00 EDT after spring forward)
    trig = ScheduleTrigger(type="schedule", mode="daily", time="09:00")
    karachi_due = compute_due_slots(trig, "Asia/Karachi", datetime(2026, 11, 17, 4, 2, tzinfo=UTC), grace_minutes=15)
    assert karachi_due == [datetime(2026, 11, 17, 4, 0, tzinfo=UTC)]

    # Older than 15m grace window -> skipped
    karachi_missed = compute_due_slots(trig, "Asia/Karachi", datetime(2026, 11, 17, 4, 20, tzinfo=UTC), grace_minutes=15)
    assert karachi_missed == []

    # DST spring-forward in America/New_York: 09:00 EDT = 13:00 UTC
    ny_due = compute_due_slots(trig, "America/New_York", datetime(2026, 3, 9, 13, 1, tzinfo=UTC), grace_minutes=15)
    assert ny_due == [datetime(2026, 3, 9, 13, 0, tzinfo=UTC)]

    # Idempotent scheduling + disconnect stops event triggers
    env = _seed(db_session, timezone="Asia/Karachi")
    t1, dept, owner, conn = env["t1"], env["dept"], env["owner"], env["conn"]
    sched_def = WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "schedule", "mode": "daily", "time": "09:00"},
            "nodes": [{"id": "ai_1", "type": "ai", "task": "summarize"}],
        }
    )
    wf_sched = create_department_workflow(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, payload=DeptWorkflowCreate(name="Daily Sched", definition=sched_def))
    submit_workflow_for_review(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf_sched.id)
    assert run_workflow_evaluation(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf_sched.id)["passed"] is True
    activate_department_workflow(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf_sched.id)

    now_slot = datetime(2026, 11, 17, 4, 1, tzinfo=UTC)
    r_first = enqueue_due_scheduled_runs(db_session, now_utc=now_slot, grace_minutes=15)
    r_second = enqueue_due_scheduled_runs(db_session, now_utc=now_slot, grace_minutes=15)
    assert len([r for r in r_first if r.workflow_id == wf_sched.id]) == 1
    assert len([r for r in r_second if r.workflow_id == wf_sched.id]) == 0

    # Event trigger works when healthy, stops when paused or connector disconnected
    ev_def = WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "event", "source": "gmail.new_email"},
            "nodes": [{"id": "ai_1", "type": "ai", "task": "classify"}],
        }
    )
    wf_ev = create_department_workflow(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, payload=DeptWorkflowCreate(name="Ev Flow", definition=ev_def))
    submit_workflow_for_review(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf_ev.id)
    assert run_workflow_evaluation(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf_ev.id)["passed"] is True
    activate_department_workflow(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf_ev.id)

    assert len(dispatch_department_event_triggers(db_session, tenant_id=t1.id, department_id=dept.id, source="gmail.new_email", payload={"subject": "Hi"})) == 1

    pause_department_workflow(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf_ev.id)
    assert len(dispatch_department_event_triggers(db_session, tenant_id=t1.id, department_id=dept.id, source="gmail.new_email", payload={"subject": "Hi"})) == 0

    conn.status = ConnectionStatus.REVOKED
    db_session.flush()
    assert len(dispatch_department_event_triggers(db_session, tenant_id=t1.id, department_id=dept.id, source="gmail.new_email", payload={"subject": "Hi"})) == 0


def test_action_failure_handling_and_ai_transient_retry_in_executor(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    from app.ai.exceptions import AIRateLimitError
    from app.db.enums import RunStepStatus
    from app.db.models import WorkflowVersion

    env = _seed(db_session)
    t1, dept, owner = env["t1"], env["dept"], env["owner"]

    defn = WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "manual"},
            "nodes": [
                {"id": "ai_1", "type": "ai", "task": "summarize"},
                {
                    "id": "act_1",
                    "type": "action",
                    "action": "gmail.send_email",
                    "params": {"to": "customer@acme.com", "body": "{{ai_1.summary}}"},
                },
            ],
            "edges": [{"source": "ai_1", "target": "act_1"}],
        }
    )
    wf = create_department_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        department_id=dept.id,
        payload=DeptWorkflowCreate(name="Retry & Failure Flow", definition=defn),
    )
    submit_workflow_for_review(
        db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id
    )
    assert (
        run_workflow_evaluation(
            db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id
        )["passed"]
        is True
    )
    activate_department_workflow(
        db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id
    )
    ver = db_session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == wf.id, WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED
        )
    )
    assert ver is not None

    # 1. Transient AI rate limit on first attempt -> router retries and succeeds, then action fails -> RunStatus.FAILED
    monkeypatch.setattr(
        "app.workflows.action_registry.ActionDispatcher.dispatch",
        staticmethod(
            lambda **kwargs: ActionResult(
                success=False, action_type=kwargs["action_type"], error="Upstream SMTP 503 failure"
            )
        ),
    )
    groq_retry = FakeProvider(
        "groq",
        responses=[
            AIRateLimitError("429 transient"),
            json.dumps(
                {
                    "text": "Customer billing summary",
                    "confidence": 0.94,
                }
            ),
        ],
    )
    ai_svc = DepartmentAiService(providers={"groq": groq_retry})

    run_fail = Run(
        tenant_id=t1.id,
        department_id=dept.id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        trigger_source=TriggerSource.MANUAL,
        status=RunStatus.QUEUED,
        trace_id=uuid.uuid4().hex,
        input={"msg": "Need invoice"},
    )
    db_session.add(run_fail)
    db_session.flush()

    finished = execute_department_run(db_session, run_fail.id, ai_service=ai_svc)
    assert len(groq_retry.calls) == 2
    assert finished.status == RunStatus.FAILED
    assert "Upstream SMTP 503 failure" in (finished.error or "")

    steps = db_session.scalars(
        select(RunStep).where(RunStep.run_id == run_fail.id).order_by(RunStep.step_index.asc())
    ).all()
    assert len(steps) == 2
    assert steps[0].status == RunStepStatus.COMPLETED
    assert steps[1].status == RunStepStatus.FAILED
    assert "Upstream SMTP 503 failure" in (steps[1].error or "")
