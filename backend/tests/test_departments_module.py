"""Automated security, isolation, RBAC, workflow routing, and usage tests for the OpsMind Department Module."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.exceptions import AIUsageLimitExceededError
from app.ai.usage.usage_tracker import UsageTracker
from app.db.enums import (
    ConnectionStatus,
    DepartmentRole,
    DepartmentStatus,
    EventStatus,
    Region,
    Role,
    RunStatus,
    WorkflowVersionStatus,
)
from app.db.models import Run, Workflow, WorkflowVersion
from app.db.models.connection import Connection
from app.db.models.event import Event
from app.db.models.membership import Membership
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.schemas.department import DepartmentCreate, DepartmentUpdate
from app.schemas.workflow import NodeDefinition, NodePosition, StepType, WorkflowDAG
from app.services.department import (
    assign_members_to_department,
    check_department_access,
    create_department,
    get_department_dashboard,
    list_departments,
    list_templates,
    remove_member_from_department,
    set_department_status,
    update_department,
)
from app.services.member import list_members
from app.workflows.actions import ActionDispatcher
from app.workflows.dept_executor import WorkflowEngine, _validate_workflow_connections


@pytest.fixture()
def db_session():
    """Provides a transactional PostgreSQL session with SAVEPOINT support that rolls back cleanly."""
    engine = get_engine()
    conn = engine.connect()
    outer_tx = conn.begin()
    session = Session(
        bind=conn,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        outer_tx.rollback()
        conn.close()


def _seed_tenant_and_users(db: Session):
    """Creates two isolated tenants and a full RBAC roster in Tenant A."""
    owner = User(id=uuid.uuid4(), email=f"owner-{uuid.uuid4().hex[:6]}@acme.com", full_name="Alice Owner")
    builder_hr = User(id=uuid.uuid4(), email=f"builder-hr-{uuid.uuid4().hex[:6]}@acme.com", full_name="Bob HR Builder")
    builder_unassigned = User(
        id=uuid.uuid4(), email=f"builder-un-{uuid.uuid4().hex[:6]}@acme.com", full_name="Charlie Unassigned"
    )
    viewer_hr = User(id=uuid.uuid4(), email=f"viewer-hr-{uuid.uuid4().hex[:6]}@acme.com", full_name="Diana HR Viewer")
    outsider = User(id=uuid.uuid4(), email=f"outsider-{uuid.uuid4().hex[:6]}@globex.com", full_name="Eve Globex")
    for u in (owner, builder_hr, builder_unassigned, viewer_hr, outsider):
        set_rls_context(db, user_id=u.id)
        db.add(u)
        db.flush()

    tenant_a = Tenant(
        id=uuid.uuid4(),
        name="Acme Corp",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        region=Region.US,
        monthly_ai_cost_cap_usd=Decimal("50.00"),
    )
    set_rls_context(db, tenant_id=tenant_a.id, user_id=owner.id)
    db.add(tenant_a)
    db.flush()
    db.add_all(
        [
            Membership(tenant_id=tenant_a.id, user_id=owner.id, role=Role.OWNER),
            Membership(tenant_id=tenant_a.id, user_id=builder_hr.id, role=Role.BUILDER),
            Membership(tenant_id=tenant_a.id, user_id=builder_unassigned.id, role=Role.BUILDER),
            Membership(tenant_id=tenant_a.id, user_id=viewer_hr.id, role=Role.VIEWER),
        ]
    )
    db.flush()

    tenant_b = Tenant(
        id=uuid.uuid4(),
        name="Globex Inc",
        slug=f"globex-{uuid.uuid4().hex[:8]}",
        region=Region.US,
        monthly_ai_cost_cap_usd=Decimal("50.00"),
    )
    set_rls_context(db, tenant_id=tenant_b.id, user_id=outsider.id)
    db.add(tenant_b)
    db.flush()
    db.add(Membership(tenant_id=tenant_b.id, user_id=outsider.id, role=Role.OWNER))
    db.flush()

    set_rls_context(db, tenant_id=tenant_a.id, user_id=owner.id)
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "owner": owner,
        "builder_hr": builder_hr,
        "builder_unassigned": builder_unassigned,
        "viewer_hr": viewer_hr,
        "outsider": outsider,
    }


class TestDepartmentModule:
    def test_templates_and_department_crud_and_duplicate_prevention(self, db_session: Session):
        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]

        templates = list_templates()
        assert len(templates) == 5
        assert {t.key for t in templates} == {"support", "finance", "sales", "legal", "operations"}

        legal_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="Legal & Compliance", template_key="legal"),
        )
        assert legal_dept.code == "legal"
        assert legal_dept.status == DepartmentStatus.ACTIVE
        assert legal_dept.settings.get("require_human_review") is True

        # Duplicate code or name in same company must fail with 409
        with pytest.raises(HTTPException) as exc:
            create_department(
                db_session,
                tenant_id=tenant_a.id,
                actor_id=owner.id,
                actor_role=Role.OWNER,
                payload=DepartmentCreate(name="Legal Duplicate", code="legal"),
            )
        assert exc.value.status_code == 409

    def test_company_members_assignment_status_and_cross_tenant_protection(self, db_session: Session):
        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]
        builder_hr = ctx["builder_hr"]
        outsider = ctx["outsider"]

        hr_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="Human Resources", code="hr"),
        )

        # Initially all members are unassigned
        members_before = list_members(db_session, tenant_a.id)
        hr_member_before = next(m for m in members_before if m.user_id == builder_hr.id)
        assert hr_member_before.assignment_status == "unassigned"
        assert hr_member_before.departments == []

        # Assign builder_hr to HR department
        assigned_list = assign_members_to_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            user_ids=[builder_hr.id],
        )
        assert len(assigned_list) == 1
        assert assigned_list[0].user_id == builder_hr.id
        assert assigned_list[0].role == Role.BUILDER

        members_after = list_members(db_session, tenant_a.id)
        hr_member_after = next(m for m in members_after if m.user_id == builder_hr.id)
        assert hr_member_after.assignment_status == "assigned"
        assert len(hr_member_after.departments) == 1
        assert hr_member_after.departments[0].code == "hr"

        # Outsider from Tenant B cannot be assigned to Tenant A's department
        with pytest.raises(HTTPException) as exc:
            assign_members_to_department(
                db_session,
                tenant_id=tenant_a.id,
                actor_id=owner.id,
                actor_role=Role.OWNER,
                department_id=hr_dept.id,
                user_ids=[outsider.id],
            )
        assert exc.value.status_code == 404

        # Remove member from department -> returns to unassigned company member
        remove_member_from_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            target_user_id=builder_hr.id,
        )
        members_removed = list_members(db_session, tenant_a.id)
        hr_member_removed = next(m for m in members_removed if m.user_id == builder_hr.id)
        assert hr_member_removed.assignment_status == "unassigned"

    def test_rbac_department_isolation_for_builder_and_viewer(self, db_session: Session):
        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]
        builder_hr = ctx["builder_hr"]
        builder_unassigned = ctx["builder_unassigned"]
        viewer_hr = ctx["viewer_hr"]

        hr_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="Human Resources", code="hr"),
        )
        sales_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="Sales", code="sales"),
        )

        assign_members_to_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            user_ids=[builder_hr.id, viewer_hr.id],
        )

        # Unassigned Builder sees 0 departments and cannot access HR
        unassigned_depts = list_departments(
            db_session, tenant_id=tenant_a.id, user_id=builder_unassigned.id, role=Role.BUILDER
        )
        assert unassigned_depts == []
        with pytest.raises(HTTPException) as exc:
            check_department_access(
                db_session,
                tenant_id=tenant_a.id,
                department_id=hr_dept.id,
                user_id=builder_unassigned.id,
                role=Role.BUILDER,
                require_write=False,
            )
        assert exc.value.status_code == 403

        # HR Builder sees only HR, can read/write HR, but is blocked from Sales
        hr_builder_depts = list_departments(
            db_session, tenant_id=tenant_a.id, user_id=builder_hr.id, role=Role.BUILDER
        )
        assert [d.id for d in hr_builder_depts] == [hr_dept.id]
        check_department_access(
            db_session,
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            user_id=builder_hr.id,
            role=Role.BUILDER,
            require_write=True,
        )
        with pytest.raises(HTTPException) as exc_sales:
            check_department_access(
                db_session,
                tenant_id=tenant_a.id,
                department_id=sales_dept.id,
                user_id=builder_hr.id,
                role=Role.BUILDER,
                require_write=False,
            )
        assert exc_sales.value.status_code == 403

        # HR Viewer can read HR (require_write=False) but cannot write (require_write=True)
        check_department_access(
            db_session,
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            user_id=viewer_hr.id,
            role=Role.VIEWER,
            require_write=False,
        )
        with pytest.raises(HTTPException) as exc_viewer:
            check_department_access(
                db_session,
                tenant_id=tenant_a.id,
                department_id=hr_dept.id,
                user_id=viewer_hr.id,
                role=Role.VIEWER,
                require_write=True,
            )
        assert exc_viewer.value.status_code == 403

    def test_cross_department_connection_isolation(self, db_session: Session):
        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]

        hr_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="HR", code="hr"),
        )
        sales_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="Sales", code="sales"),
        )

        hr_conn = Connection(
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            provider="gmail",
            display_name="HR Gmail",
            external_account_id="hr@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
        sales_conn = Connection(
            tenant_id=tenant_a.id,
            department_id=sales_dept.id,
            provider="gmail",
            display_name="Sales Gmail",
            external_account_id="sales@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
        company_conn = Connection(
            tenant_id=tenant_a.id,
            department_id=None,
            provider="gmail",
            display_name="Company Gmail",
            external_account_id="info@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
        db_session.add_all([hr_conn, sales_conn, company_conn])
        db_session.flush()

        def _dag_with_conn(conn_id: uuid.UUID) -> WorkflowDAG:
            return WorkflowDAG(
                nodes=[
                    NodeDefinition(
                        id="trig_1",
                        type=StepType.TRIGGER,
                        label="Email Trigger",
                        position=NodePosition(x=0, y=0),
                        config={"connection_id": str(conn_id)},
                    )
                ],
                edges=[],
            )

        # HR workflow can use HR connection
        _validate_workflow_connections(
            db_session,
            tenant_id=tenant_a.id,
            workflow_department_id=hr_dept.id,
            trigger_connection_id=hr_conn.id,
            dag=_dag_with_conn(hr_conn.id),
        )

        # HR workflow CANNOT use Sales connection (403)
        with pytest.raises(HTTPException) as exc1:
            _validate_workflow_connections(
                db_session,
                tenant_id=tenant_a.id,
                workflow_department_id=hr_dept.id,
                trigger_connection_id=sales_conn.id,
                dag=_dag_with_conn(sales_conn.id),
            )
        assert exc1.value.status_code == 403

        # HR workflow CANNOT use Company-wide connection (403)
        with pytest.raises(HTTPException) as exc2:
            _validate_workflow_connections(
                db_session,
                tenant_id=tenant_a.id,
                workflow_department_id=hr_dept.id,
                trigger_connection_id=company_conn.id,
                dag=_dag_with_conn(company_conn.id),
            )
        assert exc2.value.status_code == 403

        # Company-wide workflow CANNOT use HR connection (403)
        with pytest.raises(HTTPException) as exc3:
            _validate_workflow_connections(
                db_session,
                tenant_id=tenant_a.id,
                workflow_department_id=None,
                trigger_connection_id=hr_conn.id,
                dag=_dag_with_conn(hr_conn.id),
            )
        assert exc3.value.status_code == 403

        # ActionDispatcher._active_connection strictly isolates by department_id
        assert (
            ActionDispatcher._active_connection(
                db_session, tenant_a.id, "gmail", department_id=hr_dept.id
            ).id
            == hr_conn.id
        )
        assert (
            ActionDispatcher._active_connection(
                db_session, tenant_a.id, "gmail", department_id=sales_dept.id
            ).id
            == sales_conn.id
        )
        assert (
            ActionDispatcher._active_connection(
                db_session, tenant_a.id, "gmail", department_id=None
            ).id
            == company_conn.id
        )

    def test_department_event_routing_isolation_and_deactivation_lifecycle(self, db_session: Session):
        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]

        hr_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="HR", code="hr"),
        )
        sales_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="Sales", code="sales"),
        )

        hr_conn = Connection(
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            provider="gmail",
            display_name="HR Gmail",
            external_account_id="hr@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
        db_session.add(hr_conn)
        db_session.flush()

        simple_dag = {
            "nodes": [
                {"id": "n1", "type": "trigger", "label": "Start", "position": {"x": 0, "y": 0}, "config": {}},
                {
                    "id": "n2",
                    "type": "classify",
                    "label": "Classify",
                    "position": {"x": 100, "y": 0},
                    "config": {"categories": ["recruitment", "payroll"]},
                },
            ],
            "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
        }

        # Create published HR workflow and published Sales workflow
        hr_wf = Workflow(
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            name="HR Pipeline",
            created_by=owner.id,
        )
        sales_wf = Workflow(
            tenant_id=tenant_a.id,
            department_id=sales_dept.id,
            name="Sales Pipeline",
            created_by=owner.id,
        )
        db_session.add_all([hr_wf, sales_wf])
        db_session.flush()

        hr_v1 = WorkflowVersion(
            tenant_id=tenant_a.id,
            workflow_id=hr_wf.id,
            version_number=1,
            status=WorkflowVersionStatus.PUBLISHED,
            definition=simple_dag,
            trigger_event_type="email.received",
        )
        sales_v1 = WorkflowVersion(
            tenant_id=tenant_a.id,
            workflow_id=sales_wf.id,
            version_number=1,
            status=WorkflowVersionStatus.PUBLISHED,
            definition=simple_dag,
            trigger_event_type="email.received",
        )
        db_session.add_all([hr_v1, sales_v1])
        db_session.flush()

        # Incoming event on HR connection with spoofed department_id in payload
        hr_event = Event(
            tenant_id=tenant_a.id,
            connection_id=hr_conn.id,
            event_type="email.received",
            dedup_key=f"msg_{uuid.uuid4().hex}",
            status=EventStatus.RECEIVED,
            payload={
                "subject": "Candidate Resume for Software Engineer",
                "body": "Attached resume for recruitment review.",
                "department_id": str(sales_dept.id),  # Spoof attempt!
                "tenant_id": str(uuid.uuid4()),  # Spoof attempt!
            },
        )
        db_session.add(hr_event)
        db_session.flush()

        # Process pending events -> should trigger ONLY HR workflow, never Sales workflow
        triggered_count = WorkflowEngine.process_pending_events(db_session, tenant_a.id)
        assert triggered_count == 1
        runs = db_session.scalars(select(Run).where(Run.tenant_id == tenant_a.id)).all()
        assert len(runs) == 1
        run = runs[0]
        assert run.workflow_version_id == hr_v1.id
        assert run.department_id == hr_dept.id
        # Verify spoofed payload fields were stripped from input
        assert "department_id" not in (run.input or {})
        assert "tenant_id" not in (run.input or {})

        # Idempotency check: re-running process_pending_events produces 0 duplicate runs
        duplicate_count = WorkflowEngine.process_pending_events(db_session, tenant_a.id)
        assert duplicate_count == 0

        # Deactivate HR department -> auto-pauses HR workflow without deleting connections/runs
        set_department_status(
            db_session,
            tenant_id=tenant_a.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            new_status=DepartmentStatus.INACTIVE,
        )
        db_session.refresh(hr_v1)
        assert hr_v1.status == WorkflowVersionStatus.PAUSED

        # New event on inactive HR department does not trigger any workflows
        hr_event_2 = Event(
            tenant_id=tenant_a.id,
            connection_id=hr_conn.id,
            event_type="email.received",
            dedup_key=f"msg_{uuid.uuid4().hex}",
            status=EventStatus.RECEIVED,
            payload={"subject": "Another HR email"},
        )
        db_session.add(hr_event_2)
        db_session.flush()
        runs_after_deactivate = WorkflowEngine.process_pending_events(db_session, tenant_a.id)
        assert runs_after_deactivate == 0
        assert hr_event_2.status == EventStatus.IGNORED

        # Activate and archive lifecycle preserves historical runs & connections
        set_department_status(
            db_session,
            tenant_id=tenant_a.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            new_status=DepartmentStatus.ACTIVE,
        )
        archived_out = set_department_status(
            db_session,
            tenant_id=tenant_a.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            new_status=DepartmentStatus.ARCHIVED,
        )
        assert archived_out.status == DepartmentStatus.ARCHIVED

    def test_department_ai_cost_cap_and_dashboard_telemetry(self, db_session: Session):
        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]

        # Department cap exceeding Tenant cap ($50.00) must be rejected (422)
        with pytest.raises(HTTPException) as exc:
            create_department(
                db_session,
                tenant_id=tenant_a.id,
                actor_id=owner.id,
                actor_role=Role.OWNER,
                payload=DepartmentCreate(
                    name="Finance",
                    code="fin",
                    monthly_ai_cost_cap_usd=Decimal("100.00"),
                ),
            )
        assert exc.value.status_code == 422

        fin_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(
                name="Finance",
                code="fin",
                monthly_ai_cost_cap_usd=Decimal("0.01"),
            ),
        )

        # Record AI usage attributed to Finance department
        UsageTracker.set_in_memory_cap(tenant_a.id, Decimal("50.00"))
        cost_usd, _, record = UsageTracker.record_usage(
            db=db_session,
            tenant_id=tenant_a.id,
            department_id=fin_dept.id,
            provider="gemini",
            model="gemini-2.5-pro",
            task_type="extraction",
            prompt_tokens=5000,
            completion_tokens=2000,
            latency_ms=120,
        )
        assert record is not None
        assert record.department_id == fin_dept.id
        assert Decimal(str(record.cost_usd)) > Decimal("0.01")

        # Department budget check now raises AIUsageLimitExceededError
        with pytest.raises(AIUsageLimitExceededError):
            UsageTracker.check_budget(
                db=db_session,
                tenant_id=tenant_a.id,
                department_id=fin_dept.id,
            )

        # Raise cap and verify Department Dashboard metrics
        update_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_role=Role.OWNER,
            department_id=fin_dept.id,
            payload=DepartmentUpdate(monthly_ai_cost_cap_usd=Decimal("25.00")),
        )
        dashboard = get_department_dashboard(
            db_session,
            tenant_id=tenant_a.id,
            user_id=owner.id,
            role=Role.OWNER,
            department_id=fin_dept.id,
        )
        assert dashboard.department.code == "fin"
        assert dashboard.metrics.ai_total_tokens == 7000
        assert dashboard.metrics.ai_cost_usd > Decimal("0")


class TestDepartmentRolesAndConnectionLifecycle:
    def test_role_resolver_null_fallback_override_and_multi_department(self, db_session: Session):
        from app.services.department import get_effective_department_role

        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]
        builder_hr = ctx["builder_hr"]
        viewer_hr = ctx["viewer_hr"]

        hr_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="HR", code="hr"),
        )
        sales_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="Sales", code="sales"),
        )

        # Assign builder_hr to HR with role=None (NULL fallback -> BUILDER)
        # and to Sales with role=DepartmentRole.ADMIN (override -> ADMIN)
        assign_members_to_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            user_ids=[builder_hr.id],
            role=None,
        )
        assign_members_to_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            department_id=sales_dept.id,
            user_ids=[builder_hr.id],
            role=DepartmentRole.ADMIN,
        )

        assert (
            get_effective_department_role(
                db_session,
                tenant_id=tenant_a.id,
                user_id=builder_hr.id,
                tenant_role=Role.BUILDER,
                department_id=hr_dept.id,
            )
            == Role.BUILDER
        )
        assert (
            get_effective_department_role(
                db_session,
                tenant_id=tenant_a.id,
                user_id=builder_hr.id,
                tenant_role=Role.BUILDER,
                department_id=sales_dept.id,
            )
            == Role.ADMIN
        )
        # Unassigned user returns None
        assert (
            get_effective_department_role(
                db_session,
                tenant_id=tenant_a.id,
                user_id=viewer_hr.id,
                tenant_role=Role.VIEWER,
                department_id=sales_dept.id,
            )
            is None
        )
        # Tenant Owner always keeps OWNER across all departments
        assert (
            get_effective_department_role(
                db_session,
                tenant_id=tenant_a.id,
                user_id=owner.id,
                tenant_role=Role.OWNER,
                department_id=sales_dept.id,
            )
            == Role.OWNER
        )

    def test_department_member_role_patch_permissions_last_admin_duplicate_and_audit(
        self, db_session: Session
    ):
        from app.db.enums import DepartmentRole
        from app.db.models.audit_log import AuditLog
        from app.services.department import update_department_member_role

        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]
        builder_hr = ctx["builder_hr"]
        viewer_hr = ctx["viewer_hr"]

        hr_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="HR", code="hr"),
        )
        sales_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="Sales", code="sales"),
        )

        # Assign builder_hr as HR Dept Admin, viewer_hr as HR Viewer
        assign_members_to_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            user_ids=[builder_hr.id],
            role=DepartmentRole.ADMIN,
        )
        assign_members_to_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            user_ids=[viewer_hr.id],
            role=DepartmentRole.VIEWER,
        )
        assign_members_to_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            department_id=sales_dept.id,
            user_ids=[viewer_hr.id],
            role=DepartmentRole.BUILDER,
        )

        # Duplicate assignment returns 409
        with pytest.raises(HTTPException) as exc:
            assign_members_to_department(
                db_session,
                tenant_id=tenant_a.id,
                actor_id=owner.id,
                actor_role=Role.OWNER,
                department_id=hr_dept.id,
                user_ids=[viewer_hr.id],
            )
        assert exc.value.status_code == 409

        # HR Dept Admin CANNOT add or remove members (403)
        with pytest.raises(HTTPException) as exc:
            assign_members_to_department(
                db_session,
                tenant_id=tenant_a.id,
                actor_id=builder_hr.id,
                actor_role=Role.BUILDER,
                department_id=hr_dept.id,
                user_ids=[ctx["builder_unassigned"].id],
            )
        assert exc.value.status_code == 403

        with pytest.raises(HTTPException) as exc:
            remove_member_from_department(
                db_session,
                tenant_id=tenant_a.id,
                actor_role=Role.BUILDER,
                department_id=hr_dept.id,
                target_user_id=viewer_hr.id,
                actor_id=builder_hr.id,
            )
        assert exc.value.status_code == 403

        # HR Dept Admin CAN update roles in HR
        updated = update_department_member_role(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=builder_hr.id,
            actor_role=Role.BUILDER,
            department_id=hr_dept.id,
            target_user_id=viewer_hr.id,
            new_role=DepartmentRole.BUILDER,
        )
        assert updated.effective_role == Role.BUILDER
        assert updated.department_role == DepartmentRole.BUILDER

        # HR Dept Admin CANNOT update roles in Sales (403)
        with pytest.raises(HTTPException) as exc:
            update_department_member_role(
                db_session,
                tenant_id=tenant_a.id,
                actor_id=builder_hr.id,
                actor_role=Role.BUILDER,
                department_id=sales_dept.id,
                target_user_id=viewer_hr.id,
                new_role=DepartmentRole.VIEWER,
            )
        assert exc.value.status_code == 403

        # Builder (non-admin) CANNOT update roles in Sales (403)
        with pytest.raises(HTTPException) as exc:
            update_department_member_role(
                db_session,
                tenant_id=tenant_a.id,
                actor_id=viewer_hr.id,
                actor_role=Role.VIEWER,
                department_id=sales_dept.id,
                target_user_id=viewer_hr.id,
                new_role=DepartmentRole.ADMIN,
            )
        assert exc.value.status_code == 403

        # Last department admin CANNOT be demoted or removed (409)
        with pytest.raises(HTTPException) as exc:
            update_department_member_role(
                db_session,
                tenant_id=tenant_a.id,
                actor_id=owner.id,
                actor_role=Role.OWNER,
                department_id=hr_dept.id,
                target_user_id=builder_hr.id,
                new_role=DepartmentRole.BUILDER,
            )
        assert exc.value.status_code == 409

        with pytest.raises(HTTPException) as exc:
            remove_member_from_department(
                db_session,
                tenant_id=tenant_a.id,
                actor_role=Role.OWNER,
                department_id=hr_dept.id,
                target_user_id=builder_hr.id,
                actor_id=owner.id,
            )
        assert exc.value.status_code == 409

        # Audit logs recorded for assign and role_changed
        audits = db_session.scalars(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.entity_type == "department_member",
            )
        ).all()
        actions = {a.action for a in audits}
        assert "department_member.assigned" in actions
        assert "department_member.role_changed" in actions

    def test_connection_lifecycle_pauses_workflows_and_blocks_cross_dept_at_save_and_run(
        self, db_session: Session
    ):
        from app.core.permissions import Permission
        from app.db.models.audit_log import AuditLog
        from app.schemas.workflow import EdgeDefinition, NodeDefinition, StepType, WorkflowDAG
        from app.services.department import pause_workflows_for_connection, require_department_permission
        from app.workflows.dept_executor import WorkflowEngine, _validate_workflow_connections

        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]
        builder_hr = ctx["builder_hr"]

        hr_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="HR", code="hr"),
        )
        sales_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="Sales", code="sales"),
        )
        assign_members_to_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            department_id=hr_dept.id,
            user_ids=[builder_hr.id],
            role=DepartmentRole.ADMIN,
        )

        # Dept Admin has CONNECTIONS_MANAGE in HR, denied in Sales
        _, eff = require_department_permission(
            db_session,
            tenant_id=tenant_a.id,
            user_id=builder_hr.id,
            tenant_role=Role.BUILDER,
            department_id=hr_dept.id,
            permission=Permission.CONNECTIONS_MANAGE,
        )
        assert eff == Role.ADMIN
        with pytest.raises(HTTPException) as exc:
            require_department_permission(
                db_session,
                tenant_id=tenant_a.id,
                user_id=builder_hr.id,
                tenant_role=Role.BUILDER,
                department_id=sales_dept.id,
                permission=Permission.CONNECTIONS_MANAGE,
            )
        assert exc.value.status_code == 403

        hr_conn = Connection(
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            provider="gmail",
            display_name="HR Inbox",
            external_account_id="hr@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
        sales_conn = Connection(
            tenant_id=tenant_a.id,
            department_id=sales_dept.id,
            provider="slack",
            display_name="Sales Slack",
            external_account_id="sales-slack",
            status=ConnectionStatus.HEALTHY,
        )
        db_session.add_all([hr_conn, sales_conn])
        db_session.flush()

        # Cross-department connection rejected at save
        cross_dag = WorkflowDAG(
            nodes=[
                NodeDefinition(id="n1", type=StepType.TRIGGER, label="Trigger"),
                NodeDefinition(
                    id="n2",
                    type=StepType.ACTION,
                    label="Send Slack",
                    config={"connection_id": str(sales_conn.id)},
                ),
            ],
            edges=[EdgeDefinition(id="e1", source="n1", target="n2")],
        )
        with pytest.raises(HTTPException) as exc:
            _validate_workflow_connections(
                db_session,
                tenant_id=tenant_a.id,
                workflow_department_id=hr_dept.id,
                trigger_connection_id=None,
                dag=cross_dag,
                actor_id=builder_hr.id,
                stage="save",
            )
        assert exc.value.status_code == 403

        # Published HR workflow referencing hr_conn via node config is paused on disconnect
        hr_wf = Workflow(
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            name="HR Onboarding",
            created_by=owner.id,
        )
        db_session.add(hr_wf)
        db_session.flush()

        hr_ver = WorkflowVersion(
            tenant_id=tenant_a.id,
            workflow_id=hr_wf.id,
            version_number=1,
            status=WorkflowVersionStatus.PUBLISHED,
            trigger_connection_id=hr_conn.id,
            trigger_event_type="email.received",
            definition={
                "nodes": [{"id": "n1", "type": "trigger", "label": "T", "config": {}}],
                "edges": [],
            },
        )
        db_session.add(hr_ver)
        db_session.flush()

        paused = pause_workflows_for_connection(
            db_session,
            tenant_id=tenant_a.id,
            connection_id=hr_conn.id,
            reason="connection disconnected",
            actor_id=builder_hr.id,
        )
        assert len(paused) == 1
        db_session.refresh(hr_ver)
        assert hr_ver.status == WorkflowVersionStatus.PAUSED
        assert hr_ver.pause_reason == "connection disconnected"

        # Runtime cross-department check rejects tampered version referencing sales_conn
        tampered_wf = Workflow(
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            name="Tampered WF",
            created_by=owner.id,
        )
        db_session.add(tampered_wf)
        db_session.flush()
        tampered_ver = WorkflowVersion(
            tenant_id=tenant_a.id,
            workflow_id=tampered_wf.id,
            version_number=1,
            status=WorkflowVersionStatus.PUBLISHED,
            trigger_connection_id=sales_conn.id,
            definition={
                "nodes": [{"id": "n1", "type": "trigger", "label": "T", "config": {}}],
                "edges": [],
            },
        )
        db_session.add(tampered_ver)
        db_session.flush()

        run = WorkflowEngine.start_run(
            db=db_session,
            tenant_id=tenant_a.id,
            workflow_id=tampered_wf.id,
            input_data={"message": "hello"},
        )
        assert run.status == RunStatus.FAILED
        assert "cross-department" in (run.error or "").lower()
        runtime_audit = db_session.scalar(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.action == "workflow.cross_department_connection_rejected",
            )
        )
        assert runtime_audit is not None

    def test_action_dispatcher_never_falls_back_to_null_department_connection(
        self, db_session: Session
    ):
        from app.workflows.actions import ActionDispatcher

        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]

        hr_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="HR", code="hr"),
        )

        # Create a legacy/company-wide connection with department_id=NULL
        null_conn = Connection(
            tenant_id=tenant_a.id,
            department_id=None,
            provider="slack",
            display_name="Legacy Company Slack",
            external_account_id="legacy-slack",
            status=ConnectionStatus.HEALTHY,
        )
        db_session.add(null_conn)
        db_session.flush()

        # Department lookup MUST NOT fall back to department_id=NULL
        resolved = ActionDispatcher._active_connection(
            db_session, tenant_a.id, "slack", department_id=hr_dept.id
        )
        assert resolved is None

        # Company-wide lookup (department_id=None) still finds it
        company_resolved = ActionDispatcher._active_connection(
            db_session, tenant_a.id, "slack", department_id=None
        )
        assert company_resolved is not None
        assert company_resolved.id == null_conn.id

    def test_connection_revoke_permanent_delete_node_level_pause_and_null_dept_rejection(
        self, db_session: Session
    ):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.connectors.sql_repo import SqlConnectionRepo
        from app.core.config import get_settings
        from app.schemas.workflow import EdgeDefinition, NodeDefinition, StepType, WorkflowDAG
        from app.workflows.dept_executor import WorkflowEngine, _validate_workflow_connections

        ctx = _seed_tenant_and_users(db_session)
        tenant_a = ctx["tenant_a"]
        owner = ctx["owner"]

        hr_dept = create_department(
            db_session,
            tenant_id=tenant_a.id,
            actor_id=owner.id,
            actor_role=Role.OWNER,
            payload=DepartmentCreate(name="HR", code="hr"),
        )

        # (d) Legacy connection with department_id=NULL rejected by department workflow at save, publish, and run
        legacy_conn = Connection(
            tenant_id=tenant_a.id,
            department_id=None,
            provider="gmail",
            display_name="Legacy Gmail",
            external_account_id="legacy@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
        db_session.add(legacy_conn)
        db_session.flush()

        legacy_dag = WorkflowDAG(
            nodes=[
                NodeDefinition(id="n1", type=StepType.TRIGGER, label="T"),
                NodeDefinition(
                    id="n2",
                    type=StepType.ACTION,
                    label="Email",
                    config={"connection_id": str(legacy_conn.id)},
                ),
            ],
            edges=[EdgeDefinition(id="e1", source="n1", target="n2")],
        )
        for stage in ("save", "publish"):
            with pytest.raises(HTTPException) as exc:
                _validate_workflow_connections(
                    db_session,
                    tenant_id=tenant_a.id,
                    workflow_department_id=hr_dept.id,
                    trigger_connection_id=None,
                    dag=legacy_dag,
                    actor_id=owner.id,
                    stage=stage,
                )
            assert exc.value.status_code == 403

        bad_wf = Workflow(
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            name="Bad Legacy Ref WF",
            created_by=owner.id,
        )
        db_session.add(bad_wf)
        db_session.flush()
        bad_ver = WorkflowVersion(
            tenant_id=tenant_a.id,
            workflow_id=bad_wf.id,
            version_number=1,
            status=WorkflowVersionStatus.PUBLISHED,
            trigger_connection_id=legacy_conn.id,
            definition={"nodes": [{"id": "n1", "type": "trigger", "label": "T", "config": {}}], "edges": []},
        )
        db_session.add(bad_ver)
        db_session.flush()

        bad_run = WorkflowEngine.start_run(
            db=db_session,
            tenant_id=tenant_a.id,
            workflow_id=bad_wf.id,
            input_data={"message": "test"},
        )
        assert bad_run.status == RunStatus.FAILED
        assert "cross-department" in (bad_run.error or "").lower()

        # (c) Node-level config["connection_id"] reference (trigger_connection_id=None) is paused
        hr_node_conn = Connection(
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            provider="slack",
            display_name="HR Slack",
            external_account_id="hr-slack",
            status=ConnectionStatus.HEALTHY,
        )
        db_session.add(hr_node_conn)
        db_session.flush()

        node_wf = Workflow(
            tenant_id=tenant_a.id,
            department_id=hr_dept.id,
            name="Node Ref WF",
            created_by=owner.id,
        )
        db_session.add(node_wf)
        db_session.flush()
        node_ver = WorkflowVersion(
            tenant_id=tenant_a.id,
            workflow_id=node_wf.id,
            version_number=1,
            status=WorkflowVersionStatus.PUBLISHED,
            trigger_connection_id=None,
            definition={
                "nodes": [
                    {"id": "n1", "type": "trigger", "label": "T", "config": {}},
                    {
                        "id": "n2",
                        "type": "action",
                        "label": "Post",
                        "config": {"connection_id": str(hr_node_conn.id)},
                    },
                ],
                "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
            },
        )
        db_session.add(node_ver)
        db_session.flush()

        from app.services.department import pause_workflows_for_connection

        paused_node = pause_workflows_for_connection(
            db_session,
            tenant_id=tenant_a.id,
            connection_id=hr_node_conn.id,
            reason="connection disconnected",
            actor_id=owner.id,
        )
        assert len(paused_node) == 1
        db_session.refresh(node_ver)
        assert node_ver.status == WorkflowVersionStatus.PAUSED
        assert node_ver.pause_reason == "connection disconnected"

        # (b) Permanent delete (?permanent=true via SqlConnectionRepo.delete) pauses published versions
        # BEFORE trigger_connection_id is nulled, in the same transaction.
        from sqlalchemy import create_engine
        from sqlalchemy import text as sa_text

        from app.workflows.dept_executor import _run_coroutine_sync

        settings = get_settings()
        real_engine = create_engine(settings.app_database_url or settings.database_url)
        perm_tenant_id = uuid.uuid4()
        perm_user_id = uuid.uuid4()
        try:
            with Session(real_engine) as rs, rs.begin():
                set_rls_context(rs, tenant_id=perm_tenant_id, user_id=perm_user_id)
                rs.add(User(id=perm_user_id, email=f"perm-{uuid.uuid4().hex[:6]}@acme.com", full_name="Perm"))
                rs.add(Tenant(id=perm_tenant_id, name="Perm Tenant", slug=f"perm-{uuid.uuid4().hex[:6]}", region=Region.US))
                rs.flush()
                perm_dept = create_department(
                    rs,
                    tenant_id=perm_tenant_id,
                    actor_id=perm_user_id,
                    actor_role=Role.OWNER,
                    payload=DepartmentCreate(name="Perm Dept", code="perm"),
                )
                perm_conn = Connection(
                    tenant_id=perm_tenant_id,
                    department_id=perm_dept.id,
                    provider="gmail",
                    display_name="Perm Delete Gmail",
                    external_account_id="perm@acme.com",
                    status=ConnectionStatus.HEALTHY,
                )
                rs.add(perm_conn)
                rs.flush()
                perm_wf = Workflow(
                    tenant_id=perm_tenant_id,
                    department_id=perm_dept.id,
                    name="Perm Delete WF",
                    created_by=perm_user_id,
                )
                rs.add(perm_wf)
                rs.flush()
                perm_ver = WorkflowVersion(
                    tenant_id=perm_tenant_id,
                    workflow_id=perm_wf.id,
                    version_number=1,
                    status=WorkflowVersionStatus.PUBLISHED,
                    trigger_connection_id=perm_conn.id,
                    definition={"nodes": [{"id": "n1", "type": "trigger", "label": "T", "config": {}}], "edges": []},
                )
                rs.add(perm_ver)
                rs.flush()
                perm_conn_id = perm_conn.id
                perm_ver_id = perm_ver.id

            async def _run_delete():
                eng = create_async_engine(
                    settings.app_database_url or settings.database_url,
                    connect_args={"prepare_threshold": None},
                )
                sf = async_sessionmaker(eng, expire_on_commit=False)
                repo = SqlConnectionRepo(sf)
                await repo.delete(str(perm_conn_id), tenant_id=str(perm_tenant_id))
                await eng.dispose()

            _run_coroutine_sync(_run_delete())

            with Session(real_engine) as rs, rs.begin():
                set_rls_context(rs, tenant_id=perm_tenant_id, user_id=perm_user_id)
                refreshed_perm_ver = rs.get(WorkflowVersion, perm_ver_id)
                assert refreshed_perm_ver is not None
                assert refreshed_perm_ver.status == WorkflowVersionStatus.PAUSED
                assert refreshed_perm_ver.pause_reason == "connection deleted"
                assert refreshed_perm_ver.trigger_connection_id is None
                assert rs.get(Connection, perm_conn_id) is None
        finally:
            if settings.database_url:
                owner_eng = create_engine(settings.database_url)
                with owner_eng.begin() as oc:
                    oc.execute(sa_text("DELETE FROM tenants WHERE id = :t"), {"t": perm_tenant_id})
                    oc.execute(sa_text("DELETE FROM users WHERE id = :u"), {"u": perm_user_id})
                owner_eng.dispose()
            real_engine.dispose()
