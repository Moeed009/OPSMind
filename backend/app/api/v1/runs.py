"""API endpoints for Workflow Runs (Execution & Telemetry)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import TenantContext
from app.api.rbac import require_permission
from app.core.permissions import Permission
from app.db.enums import RunStatus, TriggerSource, WorkflowVersionStatus
from app.db.models.run import Run
from app.db.models.run_step import RunStep
from app.db.models.workflow import Workflow
from app.db.models.workflow_version import WorkflowVersion
from app.schemas.run import RunOut, RunStepOut, RunTriggerRequest
from app.services import department as department_service
from app.workflows.dept_executor import execute_department_run, replay_department_run

router = APIRouter(prefix="/runs", tags=["runs"])

@router.post("/workflows/{workflow_id}/execute", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def trigger_workflow_run(
    workflow_id: uuid.UUID,
    payload: RunTriggerRequest,
    tc: Annotated[TenantContext, Depends(require_permission(Permission.WORKFLOWS_READ))],
    background: Annotated[bool, Query(description="Attempt Celery background dispatch with sync fallback")] = False,
) -> RunOut:
    """Triggers a workflow run (via Celery worker when background=True and broker is reachable, or synchronously)."""
    wf = tc.db.scalar(
        select(Workflow).where(Workflow.tenant_id == tc.tenant_id, Workflow.id == workflow_id)
    )
    if not wf:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found.")
    if wf.department_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Legacy workflows (department_id is null) are read-only and cannot be executed.",
        )
    if wf.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Archived workflows cannot be executed.")

    department_service.check_department_access(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        role=tc.role,
        department_id=wf.department_id,
        require_write=True,
    )

    ver = tc.db.scalar(
        select(WorkflowVersion)
        .where(
            WorkflowVersion.tenant_id == tc.tenant_id,
            WorkflowVersion.workflow_id == wf.id,
            WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
        )
        .order_by(WorkflowVersion.version_number.desc())
    )
    if ver is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow has no active published version.")

    input_payload = payload.input_data or {"message": "Test customer inquiry", "subject": "Support Request"}
    if background:
        try:
            from app.workflows.tasks import execute_workflow_run_task

            async_res = execute_workflow_run_task.apply_async(
                args=[str(tc.tenant_id), str(workflow_id), input_payload, None],
                retry=False,
            )
            res_data = async_res.get(timeout=15)
            if isinstance(res_data, dict) and res_data.get("run_id"):
                return get_run_details(uuid.UUID(str(res_data["run_id"])), tc)
        except Exception:
            pass

    new_run = Run(
        tenant_id=tc.tenant_id,
        department_id=wf.department_id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        trigger_source=TriggerSource.MANUAL,
        status=RunStatus.QUEUED,
        trace_id=uuid.uuid4().hex,
        input=input_payload,
    )
    tc.db.add(new_run)
    tc.db.flush()
    run = execute_department_run(tc.db, new_run.id)

    steps = tc.db.scalars(
        select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.step_index.asc())
    ).all()

    return RunOut(
        id=run.id,
        tenant_id=run.tenant_id,
        department_id=run.department_id,
        workflow_version_id=run.workflow_version_id,
        trigger_source=run.trigger_source,
        status=run.status,
        trace_id=run.trace_id,
        input=run.input,
        output=run.output,
        error=run.error,
        cost_usd=run.cost_usd,
        total_tokens=run.total_tokens,
        started_at=run.started_at,
        finished_at=run.finished_at,
        steps=[RunStepOut.model_validate(s, from_attributes=True) for s in steps],
    )

@router.get("", response_model=list[RunOut])
def list_runs(
    tc: Annotated[TenantContext, Depends(require_permission(Permission.RUNS_READ))],
    workflow_id: Annotated[uuid.UUID | None, Query()] = None,
    department_id: Annotated[uuid.UUID | None, Query()] = None,
    exclude_legacy: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[RunOut]:
    """Lists recent workflow runs for the tenant."""
    stmt = select(Run).where(Run.tenant_id == tc.tenant_id)
    if exclude_legacy:
        stmt = stmt.where(Run.department_id.is_not(None))
    if workflow_id:
        stmt = stmt.where(Run.workflow_version_id == workflow_id)
    if department_id is not None:
        department_service.check_department_access(
            tc.db,
            tenant_id=tc.tenant_id,
            user_id=tc.user.id,
            role=tc.role,
            department_id=department_id,
            require_write=False,
        )
        stmt = stmt.where(Run.department_id == department_id)
    elif not department_service.is_company_admin(tc.role):
        assigned = department_service.get_assigned_department_ids(tc.db, tc.tenant_id, tc.user.id)
        if assigned:
            stmt = stmt.where(Run.department_id.is_(None) | Run.department_id.in_(assigned))
        else:
            stmt = stmt.where(Run.department_id.is_(None))

    stmt = stmt.order_by(Run.created_at.desc()).limit(limit)
    runs = tc.db.scalars(stmt).all()

    results: list[RunOut] = []
    for r in runs:
        results.append(
            RunOut(
                id=r.id,
                tenant_id=r.tenant_id,
                department_id=r.department_id,
                workflow_version_id=r.workflow_version_id,
                trigger_source=r.trigger_source,
                status=r.status,
                trace_id=r.trace_id,
                input=r.input,
                output=r.output,
                error=r.error,
                cost_usd=r.cost_usd,
                total_tokens=r.total_tokens,
                started_at=r.started_at,
                finished_at=r.finished_at,
                steps=[],
            )
        )
    return results

@router.get("/{run_id}", response_model=RunOut)
def get_run_details(
    run_id: uuid.UUID,
    tc: Annotated[TenantContext, Depends(require_permission(Permission.RUNS_READ))],
) -> RunOut:
    """Returns detailed telemetry, node inputs/outputs, and step timeline for a run."""
    run = tc.db.scalar(select(Run).where(Run.tenant_id == tc.tenant_id, Run.id == run_id))
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    if run.department_id is not None:
        department_service.check_department_access(
            tc.db,
            tenant_id=tc.tenant_id,
            user_id=tc.user.id,
            role=tc.role,
            department_id=run.department_id,
            require_write=False,
        )

    steps = tc.db.scalars(
        select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.step_index.asc())
    ).all()

    return RunOut(
        id=run.id,
        tenant_id=run.tenant_id,
        department_id=run.department_id,
        workflow_version_id=run.workflow_version_id,
        trigger_source=run.trigger_source,
        status=run.status,
        trace_id=run.trace_id,
        input=run.input,
        output=run.output,
        error=run.error,
        cost_usd=run.cost_usd,
        total_tokens=run.total_tokens,
        started_at=run.started_at,
        finished_at=run.finished_at,
        steps=[RunStepOut.model_validate(s, from_attributes=True) for s in steps],
    )


@router.post("/{run_id}/replay", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def replay_run_endpoint(
    run_id: uuid.UUID,
    tc: Annotated[TenantContext, Depends(require_permission(Permission.RUNS_REPLAY))],
) -> RunOut:
    """Replay an existing workflow run with its original trigger input."""
    orig = tc.db.scalar(select(Run).where(Run.tenant_id == tc.tenant_id, Run.id == run_id))
    if not orig:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    if orig.department_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Legacy workflow runs (department_id is null) are read-only and cannot be replayed.",
        )
    new_run = replay_department_run(
        tc.db,
        tenant_id=tc.tenant_id,
        department_id=orig.department_id,
        run_id=orig.id,
        user_id=tc.user.id,
        tenant_role=tc.role,
    )
    steps = tc.db.scalars(
        select(RunStep).where(RunStep.run_id == new_run.id).order_by(RunStep.step_index.asc())
    ).all()
    return RunOut(
        id=new_run.id,
        tenant_id=new_run.tenant_id,
        department_id=new_run.department_id,
        workflow_version_id=new_run.workflow_version_id,
        trigger_source=new_run.trigger_source,
        status=new_run.status,
        trace_id=new_run.trace_id,
        input=new_run.input,
        output=new_run.output,
        error=new_run.error,
        cost_usd=new_run.cost_usd,
        total_tokens=new_run.total_tokens,
        started_at=new_run.started_at,
        finished_at=new_run.finished_at,
        steps=[RunStepOut.model_validate(s, from_attributes=True) for s in steps],
    )
