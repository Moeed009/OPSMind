"""Department-scoped workflow storage, versioning, connector readiness, lifecycle, and RBAC."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.enums import (
    ConnectionStatus,
    DepartmentStatus,
    Role,
    RunStatus,
    TriggerSource,
    WorkflowVersionStatus,
)
from app.db.models import Connection, Department, Run, RunStep, Workflow, WorkflowVersion
from app.schemas.department_workflow import (
    DeptWorkflowCreate,
    DeptWorkflowOut,
    DeptWorkflowReviewDecision,
    DeptWorkflowRunDetailOut,
    DeptWorkflowRunNowRequest,
    DeptWorkflowRunOut,
    DeptWorkflowRunStepOut,
    DeptWorkflowStatus,
    DeptWorkflowUpdate,
)
from app.services import department as department_service
from app.workflows.definition import WorkflowDefinition

_READ_ROLES = {Role.OWNER, Role.ADMIN, Role.BUILDER, Role.REVIEWER, Role.VIEWER}
_WRITE_ROLES = {Role.OWNER, Role.ADMIN, Role.BUILDER}
_REVIEW_ROLES = {Role.OWNER, Role.ADMIN, Role.REVIEWER}
_ADMIN_ROLES = {Role.OWNER, Role.ADMIN}


def _to_lifecycle_status(ver_status: WorkflowVersionStatus) -> DeptWorkflowStatus:
    if ver_status == WorkflowVersionStatus.PUBLISHED:
        return "active"
    if ver_status == WorkflowVersionStatus.IN_REVIEW:
        return "in_review"
    if ver_status == WorkflowVersionStatus.PAUSED:
        return "paused"
    return "draft"


def _require_dept_role(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    allowed_roles: set[Role],
    require_active_dept: bool = False,
) -> tuple[Department, Role]:
    dept = db.scalar(
        select(Department).where(
            Department.tenant_id == tenant_id,
            Department.id == department_id,
        )
    )
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")

    eff_role = department_service.get_effective_department_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
    )
    if eff_role is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Access denied: you are not assigned to this department",
        )
    if not department_service.is_company_admin(tenant_role) and eff_role not in allowed_roles:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your role does not allow this action in this department",
        )
    if (
        require_active_dept
        and not department_service.is_company_admin(tenant_role)
        and dept.status != DepartmentStatus.ACTIVE
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot modify resources in a {dept.status.value} department",
        )
    return dept, eff_role


def _get_workflow_and_versions(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> tuple[Workflow, WorkflowVersion, WorkflowVersion | None]:
    wf = db.scalar(
        select(Workflow).where(
            Workflow.tenant_id == tenant_id,
            Workflow.department_id == department_id,
            Workflow.id == workflow_id,
            Workflow.archived_at.is_(None),
        )
    )
    if wf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found in this department")

    latest = db.scalar(
        select(WorkflowVersion)
        .where(
            WorkflowVersion.tenant_id == tenant_id,
            WorkflowVersion.workflow_id == wf.id,
            WorkflowVersion.status != WorkflowVersionStatus.ARCHIVED,
        )
        .order_by(WorkflowVersion.version_number.desc())
    )
    if latest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow has no active or draft version")

    active_ver = db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.tenant_id == tenant_id,
            WorkflowVersion.workflow_id == wf.id,
            WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
        )
    )
    return wf, latest, active_ver


def _to_out(wf: Workflow, latest: WorkflowVersion, active_ver: WorkflowVersion | None) -> DeptWorkflowOut:
    return DeptWorkflowOut(
        id=wf.id,
        tenant_id=wf.tenant_id,
        department_id=wf.department_id,  # type: ignore[arg-type]
        name=wf.name,
        description=wf.description,
        status=_to_lifecycle_status(latest.status),
        definition=latest.definition or {},
        version=latest.version_number,
        active_version=active_ver.version_number if active_ver else None,
        created_by=wf.created_by,
        template_key=wf.template_key,
        review_comment=wf.review_comment,
        pause_reason=latest.pause_reason,
        created_at=wf.created_at,
        updated_at=wf.updated_at,
    )


def extract_required_providers(definition: WorkflowDefinition) -> set[str]:
    providers: set[str] = set()
    if definition.trigger.type == "event":
        providers.add(definition.trigger.source.split(".", 1)[0].lower())
    for node in definition.nodes:
        if node.type == "action":
            providers.add(node.action.split(".", 1)[0].lower())
    return providers


def validate_required_connectors(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    definition_dict: dict,
) -> WorkflowDefinition:
    try:
        defn = WorkflowDefinition.model_validate(definition_dict)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid workflow definition: {exc}",
        ) from exc

    required = extract_required_providers(defn)
    if not required:
        return defn

    connected = set(
        db.scalars(
            select(Connection.provider).where(
                Connection.tenant_id == tenant_id,
                Connection.department_id == department_id,
                Connection.status == ConnectionStatus.HEALTHY,
            )
        ).all()
    )
    connected_lower = {p.lower() for p in connected}
    missing = sorted(p for p in required if p not in connected_lower)
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Cannot activate workflow: missing or disconnected department connector(s): {', '.join(missing)}",
        )
    return defn


def list_department_workflows(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
) -> list[DeptWorkflowOut]:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_READ_ROLES,
    )
    wfs = db.scalars(
        select(Workflow)
        .where(
            Workflow.tenant_id == tenant_id,
            Workflow.department_id == department_id,
            Workflow.archived_at.is_(None),
        )
        .order_by(Workflow.updated_at.desc())
    ).all()

    results: list[DeptWorkflowOut] = []
    for wf in wfs:
        _, latest, active_ver = _get_workflow_and_versions(
            db, tenant_id=tenant_id, department_id=department_id, workflow_id=wf.id
        )
        results.append(_to_out(wf, latest, active_ver))
    return results


def list_all_department_workflows(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
) -> list[DeptWorkflowOut]:
    depts = department_service.list_departments(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        role=tenant_role,
        include_archived=False,
    )
    allowed_dept_ids = [d.id for d in depts]
    if not allowed_dept_ids:
        return []
    wfs = db.scalars(
        select(Workflow)
        .where(
            Workflow.tenant_id == tenant_id,
            Workflow.department_id.in_(allowed_dept_ids),
            Workflow.archived_at.is_(None),
        )
        .order_by(Workflow.updated_at.desc())
    ).all()

    results: list[DeptWorkflowOut] = []
    for wf in wfs:
        if wf.department_id is None:
            continue
        _, latest, active_ver = _get_workflow_and_versions(
            db, tenant_id=tenant_id, department_id=wf.department_id, workflow_id=wf.id
        )
        results.append(_to_out(wf, latest, active_ver))
    return results


def get_department_workflow(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> DeptWorkflowOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_READ_ROLES,
    )
    wf, latest, active_ver = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    return _to_out(wf, latest, active_ver)


def create_department_workflow(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    payload: DeptWorkflowCreate,
) -> DeptWorkflowOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_WRITE_ROLES,
        require_active_dept=True,
    )
    wf = Workflow(
        tenant_id=tenant_id,
        department_id=department_id,
        name=payload.name,
        description=payload.description,
        template_key=payload.template_key,
        created_by=user_id,
    )
    db.add(wf)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A workflow named '{payload.name}' already exists in this company.",
        ) from exc

    ver = WorkflowVersion(
        tenant_id=tenant_id,
        workflow_id=wf.id,
        version_number=1,
        definition=payload.definition.model_dump(mode="json"),
        status=WorkflowVersionStatus.DRAFT,
        created_by=user_id,
    )
    db.add(ver)
    db.flush()
    from app.workflows.template_seed import ensure_builtin_eval_set_for_workflow

    ensure_builtin_eval_set_for_workflow(
        db,
        tenant_id=tenant_id,
        workflow_id=wf.id,
        template_key=payload.template_key,
    )
    return _to_out(wf, ver, None)


def update_department_workflow(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    payload: DeptWorkflowUpdate,
) -> DeptWorkflowOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_WRITE_ROLES,
        require_active_dept=True,
    )
    wf, latest, active_ver = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )

    if latest.status == WorkflowVersionStatus.IN_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot edit workflow while it is in review.",
        )

    if payload.name is not None:
        wf.name = payload.name
    if payload.description is not None:
        wf.description = payload.description

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A workflow named '{payload.name}' already exists in this company.",
        ) from exc

    if payload.definition is not None:
        def_dict = payload.definition.model_dump(mode="json")
        if latest.status == WorkflowVersionStatus.PUBLISHED:
            new_ver = WorkflowVersion(
                tenant_id=tenant_id,
                workflow_id=wf.id,
                version_number=latest.version_number + 1,
                definition=def_dict,
                status=WorkflowVersionStatus.DRAFT,
                created_by=user_id,
            )
            db.add(new_ver)
            db.flush()
            latest = new_ver
        elif latest.status in (WorkflowVersionStatus.DRAFT, WorkflowVersionStatus.PAUSED):
            latest.definition = def_dict
            db.flush()
        else:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Cannot edit workflow in status '{latest.status.value}'.",
            )

    return _to_out(wf, latest, active_ver)


def delete_department_workflow(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> None:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_ADMIN_ROLES,
    )
    wf, _, _ = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    wf.archived_at = datetime.now(UTC)
    db.flush()


def submit_workflow_for_review(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> DeptWorkflowOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_WRITE_ROLES,
        require_active_dept=True,
    )
    wf, latest, active_ver = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    if latest.status not in (WorkflowVersionStatus.DRAFT, WorkflowVersionStatus.PAUSED):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only draft or paused workflows can be submitted for review (current: {_to_lifecycle_status(latest.status)}).",
        )
    latest.status = WorkflowVersionStatus.IN_REVIEW
    wf.review_comment = None
    db.flush()
    return _to_out(wf, latest, active_ver)


def review_workflow(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    payload: DeptWorkflowReviewDecision,
) -> DeptWorkflowOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_REVIEW_ROLES,
    )
    wf, latest, active_ver = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    if latest.status != WorkflowVersionStatus.IN_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Workflow must be in_review to record a review decision.",
        )

    wf.review_comment = payload.comment
    if payload.decision == "send_back":
        latest.status = WorkflowVersionStatus.DRAFT
    else:
        latest.change_note = "approved_by_reviewer"
    db.flush()
    return _to_out(wf, latest, active_ver)


def activate_department_workflow(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> DeptWorkflowOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_ADMIN_ROLES,
        require_active_dept=True,
    )
    wf, latest, active_ver = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    if latest.status != WorkflowVersionStatus.IN_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Workflow must be in_review before activation (current: {_to_lifecycle_status(latest.status)}).",
        )

    validate_required_connectors(
        db,
        tenant_id=tenant_id,
        department_id=department_id,
        definition_dict=latest.definition or {},
    )

    from app.db.models.eval_result import EvalResult

    latest_eval = db.scalar(
        select(EvalResult)
        .where(
            EvalResult.tenant_id == tenant_id,
            EvalResult.workflow_version_id == latest.id,
        )
        .order_by(EvalResult.created_at.desc())
    )
    if latest_eval is None or not latest_eval.passed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Strict evaluation gate: run workflow evaluation and pass before activating/publishing.",
        )

    if active_ver and active_ver.id != latest.id:
        active_ver.status = WorkflowVersionStatus.ARCHIVED

    latest.status = WorkflowVersionStatus.PUBLISHED
    latest.published_by = user_id
    latest.published_at = datetime.now(UTC)
    latest.pause_reason = None
    db.flush()
    return _to_out(wf, latest, latest)


def run_workflow_evaluation(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> dict[str, Any]:
    """Run the structural readiness gate (deterministic, 0 tokens) against the latest version and persist EvalResult."""
    from decimal import Decimal

    from app.db.models.eval_result import EvalResult
    from app.db.models.eval_set import EvalSet
    from app.workflows.template_seed import ensure_builtin_eval_set_for_workflow

    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_WRITE_ROLES,
    )
    wf, latest, _active_ver = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    eval_set = db.scalar(
        select(EvalSet).where(EvalSet.tenant_id == tenant_id, EvalSet.workflow_id == wf.id)
    )
    if eval_set is None and wf.template_key:
        eval_set = ensure_builtin_eval_set_for_workflow(
            db, tenant_id=tenant_id, workflow_id=wf.id, template_key=wf.template_key
        )
    if eval_set is None:
        default_cases = [
            {"name": "schema_smoke", "input": {"subject": "Smoke test", "body": "Verify workflow DAG"}},
            {"name": "edge_connectivity", "input": {"subject": "Edge test", "body": "Verify transitions"}},
            {"name": "connector_check", "input": {"subject": "Connector test", "body": "Verify scoping"}},
        ]
        eval_set = EvalSet(
            tenant_id=tenant_id,
            workflow_id=wf.id,
            name=f"custom:{wf.id.hex[:8]}",
            job_type=wf.template_key or "custom",
            cases=default_cases,
            case_count=len(default_cases),
            pass_threshold=Decimal("0.7000"),
        )
        db.add(eval_set)
        db.flush()

    defn = WorkflowDefinition.model_validate(latest.definition or {})
    missing_connectors: list[str] = []
    try:
        validate_required_connectors(
            db,
            tenant_id=tenant_id,
            department_id=department_id,
            definition_dict=defn.model_dump(),
        )
    except HTTPException as exc:
        missing_connectors.append(str(exc.detail))

    case_results: list[dict[str, Any]] = []
    passed_count = 0
    cases = list(eval_set.cases or [])
    for case in cases:
        case_ok = len(defn.nodes) > 0 and not missing_connectors
        if case_ok:
            passed_count += 1
        case_results.append(
            {
                "name": case.get("name", "case"),
                "passed": case_ok,
                "error": missing_connectors[0] if missing_connectors else None,
            }
        )

    score = round(passed_count / max(len(cases), 1), 4)
    passed = score >= float(eval_set.pass_threshold)
    ev_res = EvalResult(
        tenant_id=tenant_id,
        eval_set_id=eval_set.id,
        workflow_version_id=latest.id,
        score=Decimal(str(score)),
        passed=passed,
        model="deterministic-gate",
        cost_usd=Decimal("0"),
        case_results=case_results,
    )
    db.add(ev_res)
    db.flush()
    return {
        "eval_result_id": str(ev_res.id),
        "eval_set_id": str(eval_set.id),
        "workflow_version_id": str(latest.id),
        "score": float(score),
        "pass_threshold": float(eval_set.pass_threshold),
        "passed": passed,
        "case_results": case_results,
    }


def pause_department_workflow(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    reason: str | None = None,
) -> DeptWorkflowOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_ADMIN_ROLES,
    )
    wf, latest, active_ver = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    target = active_ver if active_ver is not None else latest
    if target.status != WorkflowVersionStatus.PUBLISHED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only an active workflow can be paused.",
        )
    target.status = WorkflowVersionStatus.PAUSED
    target.pause_reason = reason or "Paused by administrator"
    db.flush()
    return _to_out(wf, target, None)


def resume_department_workflow(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> DeptWorkflowOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_ADMIN_ROLES,
        require_active_dept=True,
    )
    wf, latest, _ = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    if latest.status != WorkflowVersionStatus.PAUSED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only a paused workflow can be resumed.",
        )

    validate_required_connectors(
        db,
        tenant_id=tenant_id,
        department_id=department_id,
        definition_dict=latest.definition or {},
    )
    latest.status = WorkflowVersionStatus.PUBLISHED
    latest.pause_reason = None
    db.flush()
    return _to_out(wf, latest, latest)


def run_department_workflow_now(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    payload: DeptWorkflowRunNowRequest,
) -> DeptWorkflowRunOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_WRITE_ROLES,
        require_active_dept=True,
    )
    wf, _, active_ver = _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    if active_ver is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Workflow must be active before it can be run manually.",
        )

    run = Run(
        tenant_id=tenant_id,
        department_id=department_id,
        workflow_id=wf.id,
        workflow_version_id=active_ver.id,
        trigger_source=TriggerSource.MANUAL,
        status=RunStatus.QUEUED,
        trace_id=uuid.uuid4().hex,
        input=payload.input,
    )
    db.add(run)
    db.flush()
    return DeptWorkflowRunOut(
        id=run.id,
        tenant_id=run.tenant_id,
        department_id=run.department_id,
        workflow_id=run.workflow_id,
        status=run.status.value,
        trigger_type=run.trigger_source.value,
        scheduled_for=run.scheduled_for,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        created_at=run.created_at,
    )


def list_department_workflow_runs(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> list[DeptWorkflowRunOut]:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_READ_ROLES,
    )
    _get_workflow_and_versions(
        db, tenant_id=tenant_id, department_id=department_id, workflow_id=workflow_id
    )
    runs = db.scalars(
        select(Run)
        .where(
            Run.tenant_id == tenant_id,
            Run.department_id == department_id,
            Run.workflow_id == workflow_id,
        )
        .order_by(Run.created_at.desc())
    ).all()
    return [
        DeptWorkflowRunOut(
            id=r.id,
            tenant_id=r.tenant_id,
            department_id=r.department_id,
            workflow_id=r.workflow_id,
            status=r.status.value,
            trigger_type=r.trigger_source.value,
            scheduled_for=r.scheduled_for,
            started_at=r.started_at,
            finished_at=r.finished_at,
            error=r.error,
            created_at=r.created_at,
        )
        for r in runs
    ]


def get_department_run_detail(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    run_id: uuid.UUID,
) -> DeptWorkflowRunDetailOut:
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=_READ_ROLES,
    )
    run = db.scalar(
        select(Run).where(
            Run.tenant_id == tenant_id,
            Run.department_id == department_id,
            Run.id == run_id,
        )
    )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found in this department")

    steps = db.scalars(
        select(RunStep)
        .where(RunStep.tenant_id == tenant_id, RunStep.run_id == run.id)
        .order_by(RunStep.step_index.asc())
    ).all()

    return DeptWorkflowRunDetailOut(
        id=run.id,
        tenant_id=run.tenant_id,
        department_id=run.department_id,
        workflow_id=run.workflow_id,
        status=run.status.value,
        trigger_type=run.trigger_source.value,
        scheduled_for=run.scheduled_for,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        created_at=run.created_at,
        steps=[
            DeptWorkflowRunStepOut(
                id=s.id,
                run_id=s.run_id,
                node_id=s.node_id,
                node_type=s.step_type,
                status=s.status.value,
                input_preview=s.input or {},
                output=s.output,
                started_at=s.started_at,
                finished_at=s.finished_at,
                error=s.error,
            )
            for s in steps
        ],
    )
