"""Phase 2 tests: department workflow storage, lifecycle, versioning, connector readiness, RBAC, and tenant isolation."""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.enums import ConnectionStatus, DepartmentRole, Region, Role
from app.db.models import Connection, Membership, RunStep, Tenant, User
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.schemas.department import DepartmentCreate
from app.schemas.department_workflow import (
    DeptWorkflowCreate,
    DeptWorkflowReviewDecision,
    DeptWorkflowRunNowRequest,
    DeptWorkflowUpdate,
)
from app.services.department import assign_members_to_department, create_department
from app.services.department_workflows import (
    activate_department_workflow,
    create_department_workflow,
    delete_department_workflow,
    get_department_run_detail,
    get_department_workflow,
    list_department_workflow_runs,
    list_department_workflows,
    pause_department_workflow,
    resume_department_workflow,
    review_workflow,
    run_department_workflow_now,
    run_workflow_evaluation,
    submit_workflow_for_review,
    update_department_workflow,
)
from app.workflows.definition import WorkflowDefinition


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


def _sample_def(action: str = "gmail.send_reply") -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "event", "source": "gmail.new_email"},
            "nodes": [
                {"id": "ai_1", "type": "ai", "task": "draft_reply"},
                {"id": "act_1", "type": "action", "action": action, "params": {"body": "{{ai_1.text}}"}},
            ],
            "edges": [{"source": "ai_1", "target": "act_1"}],
        }
    )


def _seed_env(db: Session):
    owner = User(id=uuid.uuid4(), email=f"owner-{uuid.uuid4().hex[:6]}@acme.com")
    dept_admin = User(id=uuid.uuid4(), email=f"dadmin-{uuid.uuid4().hex[:6]}@acme.com")
    builder = User(id=uuid.uuid4(), email=f"builder-{uuid.uuid4().hex[:6]}@acme.com")
    reviewer = User(id=uuid.uuid4(), email=f"reviewer-{uuid.uuid4().hex[:6]}@acme.com")
    viewer = User(id=uuid.uuid4(), email=f"viewer-{uuid.uuid4().hex[:6]}@acme.com")
    unassigned = User(id=uuid.uuid4(), email=f"plain-{uuid.uuid4().hex[:6]}@acme.com")
    other_owner = User(id=uuid.uuid4(), email=f"other-{uuid.uuid4().hex[:6]}@other.com")
    for u in (owner, dept_admin, builder, reviewer, viewer, unassigned, other_owner):
        set_rls_context(db, user_id=u.id)
        db.add(u)
        db.flush()

    t1 = Tenant(id=uuid.uuid4(), name=f"Acme-{uuid.uuid4().hex[:6]}", slug=f"acme-{uuid.uuid4().hex[:6]}", region=Region.US)
    set_rls_context(db, user_id=owner.id, tenant_id=t1.id)
    db.add(t1)
    db.flush()
    db.add_all([
        Membership(tenant_id=t1.id, user_id=owner.id, role=Role.OWNER),
        Membership(tenant_id=t1.id, user_id=dept_admin.id, role=Role.BUILDER),
        Membership(tenant_id=t1.id, user_id=builder.id, role=Role.BUILDER),
        Membership(tenant_id=t1.id, user_id=reviewer.id, role=Role.VIEWER),
        Membership(tenant_id=t1.id, user_id=viewer.id, role=Role.VIEWER),
        Membership(tenant_id=t1.id, user_id=unassigned.id, role=Role.BUILDER),
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
        payload=DepartmentCreate(name="Support"),
    )
    other_dept = create_department(
        db,
        tenant_id=t1.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Sales"),
    )
    assign_members_to_department(db, tenant_id=t1.id, actor_id=owner.id, actor_role=Role.OWNER, department_id=dept.id, user_ids=[dept_admin.id], role=DepartmentRole.ADMIN)
    assign_members_to_department(db, tenant_id=t1.id, actor_id=owner.id, actor_role=Role.OWNER, department_id=dept.id, user_ids=[builder.id], role=DepartmentRole.BUILDER)
    assign_members_to_department(db, tenant_id=t1.id, actor_id=owner.id, actor_role=Role.OWNER, department_id=dept.id, user_ids=[reviewer.id], role=DepartmentRole.REVIEWER)
    assign_members_to_department(db, tenant_id=t1.id, actor_id=owner.id, actor_role=Role.OWNER, department_id=dept.id, user_ids=[viewer.id], role=DepartmentRole.VIEWER)

    return {
        "t1": t1,
        "t2": t2,
        "dept": dept,
        "other_dept": other_dept,
        "owner": owner,
        "dept_admin": dept_admin,
        "builder": builder,
        "reviewer": reviewer,
        "viewer": viewer,
        "unassigned": unassigned,
        "other_owner": other_owner,
    }


def test_lifecycle_transitions_and_connector_readiness(db_session: Session):
    env = _seed_env(db_session)
    t1, dept, other_dept = env["t1"], env["dept"], env["other_dept"]
    builder, reviewer, dept_admin = env["builder"], env["reviewer"], env["dept_admin"]

    wf = create_department_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=builder.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        payload=DeptWorkflowCreate(name="Triage", definition=_sample_def()),
    )
    assert wf.status == "draft"
    assert wf.version == 1
    assert wf.active_version is None

    wf = submit_workflow_for_review(
        db_session,
        tenant_id=t1.id,
        user_id=builder.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        workflow_id=wf.id,
    )
    assert wf.status == "in_review"

    with pytest.raises(HTTPException) as exc:
        update_department_workflow(
            db_session,
            tenant_id=t1.id,
            user_id=builder.id,
            tenant_role=Role.BUILDER,
            department_id=dept.id,
            workflow_id=wf.id,
            payload=DeptWorkflowUpdate(name="Cannot Edit In Review"),
        )
    assert exc.value.status_code == 409

    wf = review_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=reviewer.id,
        tenant_role=Role.VIEWER,
        department_id=dept.id,
        workflow_id=wf.id,
        payload=DeptWorkflowReviewDecision(decision="send_back", comment="Add confidence threshold"),
    )
    assert wf.status == "draft"
    assert wf.review_comment == "Add confidence threshold"

    wf = submit_workflow_for_review(
        db_session,
        tenant_id=t1.id,
        user_id=builder.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        workflow_id=wf.id,
    )
    wf = review_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=reviewer.id,
        tenant_role=Role.VIEWER,
        department_id=dept.id,
        workflow_id=wf.id,
        payload=DeptWorkflowReviewDecision(decision="approve", comment="LGTM"),
    )
    assert wf.status == "in_review"

    # Cross-department connector does NOT satisfy activation
    db_session.add(
        Connection(
            tenant_id=t1.id,
            department_id=other_dept.id,
            provider="gmail",
            display_name="Sales Gmail",
            external_account_id="sales@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
    )
    db_session.flush()

    with pytest.raises(HTTPException) as exc_conn:
        activate_department_workflow(
            db_session,
            tenant_id=t1.id,
            user_id=dept_admin.id,
            tenant_role=Role.BUILDER,
            department_id=dept.id,
            workflow_id=wf.id,
        )
    assert exc_conn.value.status_code == 422
    assert "gmail" in str(exc_conn.value.detail)

    # Connect gmail in THIS department -> activation succeeds
    db_session.add(
        Connection(
            tenant_id=t1.id,
            department_id=dept.id,
            provider="gmail",
            display_name="Support Gmail",
            external_account_id="support@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
    )
    db_session.flush()

    assert run_workflow_evaluation(
        db_session,
        tenant_id=t1.id,
        user_id=dept_admin.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        workflow_id=wf.id,
    )["passed"] is True
    wf = activate_department_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=dept_admin.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        workflow_id=wf.id,
    )
    assert wf.status == "active"
    assert wf.active_version == 1

    wf = pause_department_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=dept_admin.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        workflow_id=wf.id,
    )
    assert wf.status == "paused"

    wf = resume_department_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=dept_admin.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        workflow_id=wf.id,
    )
    assert wf.status == "active"


def test_versioning_keeps_active_running_while_editing_draft(db_session: Session):
    env = _seed_env(db_session)
    t1, dept, owner, builder = env["t1"], env["dept"], env["owner"], env["builder"]

    db_session.add(
        Connection(
            tenant_id=t1.id,
            department_id=dept.id,
            provider="gmail",
            display_name="Support Gmail",
            external_account_id="support@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
    )
    db_session.flush()

    wf = create_department_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=builder.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        payload=DeptWorkflowCreate(name="Versioned", definition=_sample_def()),
    )
    submit_workflow_for_review(db_session, tenant_id=t1.id, user_id=builder.id, tenant_role=Role.BUILDER, department_id=dept.id, workflow_id=wf.id)
    assert run_workflow_evaluation(
        db_session,
        tenant_id=t1.id,
        user_id=owner.id,
        tenant_role=Role.OWNER,
        department_id=dept.id,
        workflow_id=wf.id,
    )["passed"] is True
    wf = activate_department_workflow(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)
    assert wf.version == 1
    assert wf.active_version == 1

    # Editing active workflow creates draft v2 while v1 stays active
    v2 = update_department_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=builder.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        workflow_id=wf.id,
        payload=DeptWorkflowUpdate(definition=_sample_def("gmail.send_email")),
    )
    assert v2.status == "draft"
    assert v2.version == 2
    assert v2.active_version == 1

    # v1 can still be run manually while v2 is a draft
    run = run_department_workflow_now(
        db_session,
        tenant_id=t1.id,
        user_id=builder.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        workflow_id=wf.id,
        payload=DeptWorkflowRunNowRequest(input={"msg": "hello"}),
    )
    assert run.status == "queued"

    db_session.add(
        RunStep(
            tenant_id=t1.id,
            run_id=run.id,
            step_index=0,
            node_id="ai_1",
            step_type="ai",
            input={"preview": "hi"},
            output={"text": "ok"},
        )
    )
    db_session.flush()

    runs = list_department_workflow_runs(
        db_session,
        tenant_id=t1.id,
        user_id=env["viewer"].id,
        tenant_role=Role.VIEWER,
        department_id=dept.id,
        workflow_id=wf.id,
    )
    assert len(runs) == 1
    detail = get_department_run_detail(
        db_session,
        tenant_id=t1.id,
        user_id=env["viewer"].id,
        tenant_role=Role.VIEWER,
        department_id=dept.id,
        run_id=run.id,
    )
    assert len(detail.steps) == 1
    assert detail.steps[0].node_id == "ai_1"


def test_rbac_and_tenant_isolation(db_session: Session):
    env = _seed_env(db_session)
    t1, t2, dept = env["t1"], env["t2"], env["dept"]
    owner, builder, reviewer, viewer, unassigned, other_owner = (
        env["owner"],
        env["builder"],
        env["reviewer"],
        env["viewer"],
        env["unassigned"],
        env["other_owner"],
    )

    wf = create_department_workflow(
        db_session,
        tenant_id=t1.id,
        user_id=builder.id,
        tenant_role=Role.BUILDER,
        department_id=dept.id,
        payload=DeptWorkflowCreate(name="RBAC Flow", definition=_sample_def()),
    )

    # Unassigned plain member -> 403 on everything
    with pytest.raises(HTTPException) as exc_unassigned:
        list_department_workflows(db_session, tenant_id=t1.id, user_id=unassigned.id, tenant_role=Role.BUILDER, department_id=dept.id)
    assert exc_unassigned.value.status_code == 403

    # Viewer -> read ok, write 403
    assert len(list_department_workflows(db_session, tenant_id=t1.id, user_id=viewer.id, tenant_role=Role.VIEWER, department_id=dept.id)) == 1
    with pytest.raises(HTTPException) as exc_viewer:
        create_department_workflow(db_session, tenant_id=t1.id, user_id=viewer.id, tenant_role=Role.VIEWER, department_id=dept.id, payload=DeptWorkflowCreate(name="No", definition=_sample_def()))
    assert exc_viewer.value.status_code == 403

    # Builder -> cannot review, activate, pause, delete
    submit_workflow_for_review(db_session, tenant_id=t1.id, user_id=builder.id, tenant_role=Role.BUILDER, department_id=dept.id, workflow_id=wf.id)
    for fn in (
        lambda: review_workflow(db_session, tenant_id=t1.id, user_id=builder.id, tenant_role=Role.BUILDER, department_id=dept.id, workflow_id=wf.id, payload=DeptWorkflowReviewDecision(decision="approve")),
        lambda: activate_department_workflow(db_session, tenant_id=t1.id, user_id=builder.id, tenant_role=Role.BUILDER, department_id=dept.id, workflow_id=wf.id),
        lambda: delete_department_workflow(db_session, tenant_id=t1.id, user_id=builder.id, tenant_role=Role.BUILDER, department_id=dept.id, workflow_id=wf.id),
    ):
        with pytest.raises(HTTPException) as exc_b:
            fn()
        assert exc_b.value.status_code == 403

    # Reviewer -> cannot create or activate
    with pytest.raises(HTTPException) as exc_rev:
        activate_department_workflow(db_session, tenant_id=t1.id, user_id=reviewer.id, tenant_role=Role.VIEWER, department_id=dept.id, workflow_id=wf.id)
    assert exc_rev.value.status_code == 403

    # Tenant isolation -> tenant 2 gets 404
    set_rls_context(db_session, user_id=other_owner.id, tenant_id=t2.id)
    with pytest.raises(HTTPException) as exc_iso:
        get_department_workflow(db_session, tenant_id=t2.id, user_id=other_owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)
    assert exc_iso.value.status_code == 404

    # Owner can delete
    set_rls_context(db_session, user_id=owner.id, tenant_id=t1.id)
    delete_department_workflow(db_session, tenant_id=t1.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)
