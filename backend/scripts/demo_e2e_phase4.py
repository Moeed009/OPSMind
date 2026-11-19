"""End-to-end local demonstration of Phase 4: schedule trigger -> AI -> approval -> action (FakeProvider, rolled back cleanly)."""
from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers.dept_providers import FakeProvider
from app.ai.services.dept_ai_service import DepartmentAiService
from app.db.enums import ConnectionStatus, DepartmentRole, Region, Role
from app.db.models import Connection, Membership, RunStep, Tenant, User, WorkflowApproval
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.schemas.department import DepartmentCreate
from app.schemas.department_workflow import DeptWorkflowCreate
from app.services.department import assign_members_to_department, create_department
from app.services.department_workflows import (
    activate_department_workflow,
    create_department_workflow,
    submit_workflow_for_review,
)
from app.workflows import action_registry
from app.workflows.actions import ActionResult
from app.workflows.definition import WorkflowDefinition
from app.workflows.dept_executor import decide_workflow_approval, execute_department_run
from app.workflows.scheduler import enqueue_due_scheduled_runs


def main() -> None:
    engine = get_engine()
    conn = engine.connect()
    tx = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        owner = User(id=uuid.uuid4(), email=f"demo-owner-{uuid.uuid4().hex[:6]}@acme.com")
        reviewer = User(id=uuid.uuid4(), email=f"demo-rev-{uuid.uuid4().hex[:6]}@acme.com")
        for u in (owner, reviewer):
            set_rls_context(db, user_id=u.id)
            db.add(u)
            db.flush()

        t = Tenant(id=uuid.uuid4(), name=f"Demo-{uuid.uuid4().hex[:6]}", slug=f"demo-{uuid.uuid4().hex[:6]}", region=Region.US)
        set_rls_context(db, user_id=owner.id, tenant_id=t.id)
        db.add(t)
        db.flush()
        db.add_all([
            Membership(tenant_id=t.id, user_id=owner.id, role=Role.OWNER),
            Membership(tenant_id=t.id, user_id=reviewer.id, role=Role.VIEWER),
        ])
        db.flush()

        dept = create_department(db, tenant_id=t.id, actor_id=owner.id, actor_role=Role.OWNER, payload=DepartmentCreate(name="Support", timezone="Asia/Karachi"))
        assign_members_to_department(db, tenant_id=t.id, actor_id=owner.id, actor_role=Role.OWNER, department_id=dept.id, user_ids=[reviewer.id], role=DepartmentRole.REVIEWER)
        db.add(Connection(tenant_id=t.id, department_id=dept.id, provider="gmail", display_name="Demo Gmail", external_account_id="support@demo.com", status=ConnectionStatus.HEALTHY))
        db.flush()

        defn = WorkflowDefinition.model_validate({
            "trigger": {"type": "schedule", "mode": "daily", "time": "09:00"},
            "nodes": [
                {"id": "ai_draft", "type": "ai", "task": "draft_reply"},
                {"id": "human_gate", "type": "approval", "role": "reviewer", "timeout_hours": 24},
                {"id": "send_email", "type": "action", "action": "gmail.send_email", "params": {"body": "{{ai_draft.text}}"}},
            ],
            "edges": [
                {"source": "ai_draft", "target": "human_gate"},
                {"source": "human_gate", "target": "send_email"},
            ],
        })
        wf = create_department_workflow(db, tenant_id=t.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, payload=DeptWorkflowCreate(name="Daily Digest Demo", definition=defn))
        submit_workflow_for_review(db, tenant_id=t.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)
        activate_department_workflow(db, tenant_id=t.id, user_id=owner.id, tenant_role=Role.OWNER, department_id=dept.id, workflow_id=wf.id)

        # 1. Schedule trigger enqueues run at 09:00 Asia/Karachi (04:00 UTC)
        runs = enqueue_due_scheduled_runs(db, now_utc=datetime(2026, 11, 17, 4, 0, tzinfo=UTC), grace_minutes=15)
        run = next(r for r in runs if r.workflow_id == wf.id)
        print(f"1) Scheduled run queued: id={run.id} scheduled_for={run.scheduled_for.isoformat()} status={run.status.value}")

        # 2. Executor runs AI node then pauses at Approval node
        action_registry.ActionDispatcher.dispatch = staticmethod(
            lambda **kw: ActionResult(success=True, action_type=kw["action_type"], external_ref="demo_msg_1", response_data={"sent": True})
        )
        ai_svc = DepartmentAiService(providers={"groq": FakeProvider("groq", responses=[json.dumps({"text": "Daily digest summary ready.", "confidence": 0.96})])})
        execute_department_run(db, run.id, ai_service=ai_svc)
        print(f"2) After AI + Approval gate: run.status={run.status.value}")

        # 3. Reviewer approves -> Executor resumes and runs Action node
        approval = db.scalar(select(WorkflowApproval).where(WorkflowApproval.run_id == run.id))
        assert approval is not None
        decide_workflow_approval(db, tenant_id=t.id, user_id=reviewer.id, tenant_role=Role.VIEWER, department_id=dept.id, approval_id=approval.id, decision="approve", comment="Approved!", ai_service=ai_svc)
        print(f"3) After approval decision: approval.status={approval.status.value} run.status={run.status.value}")

        steps = db.scalars(select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.step_index.asc())).all()
        for st in steps:
            print(f"   Step #{st.step_index}: node={st.node_id} type={st.step_type} status={st.status.value} output={st.output}")
    finally:
        db.close()
        tx.rollback()
        conn.close()


if __name__ == "__main__":
    main()
