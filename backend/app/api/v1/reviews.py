"""API endpoints for Human-in-the-Loop Review Tasks."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import TenantContext
from app.api.rbac import require_permission
from app.core.permissions import Permission
from app.db.enums import ReviewTaskStatus, RunStatus
from app.db.models.review_task import ReviewTask
from app.db.models.run import Run
from app.db.models.workflow import Workflow
from app.db.models.workflow_version import WorkflowVersion
from app.schemas.review import ReviewTaskDecisionRequest, ReviewTaskOut

router = APIRouter(prefix="/reviews", tags=["reviews"])

@router.get("", response_model=list[ReviewTaskOut])
def list_review_tasks(
    tc: Annotated[TenantContext, Depends(require_permission(Permission.REVIEWS_READ))],
    status_filter: Annotated[str, Query(description="'all' | 'open' | 'claimed' | 'decided' | 'pending'")] = "open",
    status: Annotated[str | None, Query()] = None,
    exclude_legacy: Annotated[bool, Query()] = False,
) -> list[ReviewTaskOut]:
    """Lists pending or decided Human-in-the-Loop review tasks."""
    effective_filter = status or status_filter
    stmt = select(ReviewTask).where(ReviewTask.tenant_id == tc.tenant_id)
    if effective_filter in ("open", "pending"):
        stmt = stmt.where(ReviewTask.status.in_([ReviewTaskStatus.OPEN, ReviewTaskStatus.CLAIMED, ReviewTaskStatus.ESCALATED]))
    elif effective_filter == "decided":
        stmt = stmt.where(ReviewTask.status.in_([ReviewTaskStatus.APPROVED, ReviewTaskStatus.EDITED, ReviewTaskStatus.REJECTED]))
    elif effective_filter in ("claimed", "escalated", "approved", "rejected", "edited"):
        stmt = stmt.where(ReviewTask.status == effective_filter)

    stmt = stmt.order_by(ReviewTask.created_at.desc())
    tasks = tc.db.scalars(stmt).all()

    results: list[ReviewTaskOut] = []
    for t in tasks:
        wf_name = "Automated Pipeline"
        run = tc.db.get(Run, t.run_id)
        if exclude_legacy and (run is None or run.department_id is None):
            continue
        if run:
            ver = tc.db.get(WorkflowVersion, run.workflow_version_id)
            if ver:
                wf = tc.db.get(Workflow, ver.workflow_id)
                if wf:
                    wf_name = wf.name

        results.append(
            ReviewTaskOut(
                id=t.id,
                tenant_id=t.tenant_id,
                run_id=t.run_id,
                run_step_id=t.run_step_id,
                status=t.status,
                risk_reason=t.risk_reason,
                proposal=t.proposal,
                evidence=t.evidence,
                confidence=t.confidence,
                deadline_at=t.deadline_at,
                claimed_by=t.claimed_by,
                decided_by=t.decided_by,
                decided_at=t.decided_at,
                decision_reason=t.decision_reason,
                workflow_name=wf_name,
                department_id=run.department_id if run else None,
                is_legacy_readonly=(run is None or run.department_id is None),
            )
        )
    return results

@router.post("/{task_id}/decide", response_model=ReviewTaskOut)
def decide_review_task(
    task_id: uuid.UUID,
    payload: ReviewTaskDecisionRequest,
    tc: Annotated[TenantContext, Depends(require_permission(Permission.REVIEWS_DECIDE))],
) -> ReviewTaskOut:
    """Submits a human decision (approve, edit, or reject) for a department-scoped review task."""
    task = tc.db.scalar(
        select(ReviewTask).where(ReviewTask.tenant_id == tc.tenant_id, ReviewTask.id == task_id)
    )
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review task not found.")
    run = tc.db.get(Run, task.run_id)
    if run is None or run.department_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Legacy review tasks (department_id is null) are read-only and cannot be approved or resumed.",
        )

    decision_val = payload.decision.lower()
    if decision_val not in ("approved", "edited", "rejected"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported review decision: {payload.decision}")
    task.status = ReviewTaskStatus(decision_val)
    task.decided_by = tc.user.id
    task.decided_at = datetime.now(UTC)
    task.decision_reason = payload.reason
    if payload.correction:
        task.proposal = {**(task.proposal or {}), **payload.correction}
    if decision_val == "rejected":
        run.status = RunStatus.CANCELLED
        run.finished_at = datetime.now(UTC)
    else:
        run.status = RunStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)
    tc.db.flush()

    return ReviewTaskOut(
        id=task.id,
        tenant_id=task.tenant_id,
        run_id=task.run_id,
        run_step_id=task.run_step_id,
        status=task.status,
        risk_reason=task.risk_reason,
        proposal=task.proposal,
        evidence=task.evidence,
        confidence=task.confidence,
        deadline_at=task.deadline_at,
        claimed_by=task.claimed_by,
        decided_by=task.decided_by,
        decided_at=task.decided_at,
        decision_reason=task.decision_reason,
        department_id=run.department_id,
        is_legacy_readonly=False,
    )
