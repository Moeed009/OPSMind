from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import CurrentUser, TenantCtx
from app.db.enums import ConnectionStatus, Role, WorkflowApprovalStatus
from app.db.models import Connection, Workflow, WorkflowApproval, WorkflowTemplate
from app.schemas.department_workflow import (
    DeptWorkflowCreate,
    DeptWorkflowOut,
    DeptWorkflowReviewDecision,
    DeptWorkflowRunDetailOut,
    DeptWorkflowRunNowRequest,
    DeptWorkflowRunOut,
    DeptWorkflowUpdate,
)
from app.services import department_workflows as svc
from app.workflows.definition import WorkflowDefinition
from app.workflows.dept_executor import decide_workflow_approval, replay_department_run

router = APIRouter(prefix="/departments/{department_id}", tags=["department-workflows"])
global_templates_router = APIRouter(tags=["department-workflows"])


@router.get("/workflows", response_model=list[DeptWorkflowOut])
def list_workflows(department_id: uuid.UUID, tc: TenantCtx) -> list[DeptWorkflowOut]:
    return svc.list_department_workflows(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
    )


@router.post("/workflows", response_model=DeptWorkflowOut, status_code=status.HTTP_201_CREATED)
def create_workflow(
    department_id: uuid.UUID,
    payload: DeptWorkflowCreate,
    tc: TenantCtx,
) -> DeptWorkflowOut:
    return svc.create_department_workflow(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        payload=payload,
    )


@router.get("/workflows/{workflow_id}", response_model=DeptWorkflowOut)
def get_workflow(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tc: TenantCtx,
) -> DeptWorkflowOut:
    return svc.get_department_workflow(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
    )


@router.patch("/workflows/{workflow_id}", response_model=DeptWorkflowOut)
def update_workflow(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    payload: DeptWorkflowUpdate,
    tc: TenantCtx,
) -> DeptWorkflowOut:
    return svc.update_department_workflow(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
        payload=payload,
    )


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tc: TenantCtx,
) -> Response:
    svc.delete_department_workflow(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workflows/{workflow_id}/submit-review", response_model=DeptWorkflowOut)
def submit_review(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tc: TenantCtx,
) -> DeptWorkflowOut:
    return svc.submit_workflow_for_review(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
    )


@router.post("/workflows/{workflow_id}/review", response_model=DeptWorkflowOut)
def decide_review(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    payload: DeptWorkflowReviewDecision,
    tc: TenantCtx,
) -> DeptWorkflowOut:
    return svc.review_workflow(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
        payload=payload,
    )


@router.post("/workflows/{workflow_id}/activate", response_model=DeptWorkflowOut)
def activate_workflow(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tc: TenantCtx,
) -> DeptWorkflowOut:
    return svc.activate_department_workflow(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
    )


@router.post("/workflows/{workflow_id}/pause", response_model=DeptWorkflowOut)
def pause_workflow(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tc: TenantCtx,
) -> DeptWorkflowOut:
    return svc.pause_department_workflow(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
    )


@router.post("/workflows/{workflow_id}/resume", response_model=DeptWorkflowOut)
def resume_workflow(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tc: TenantCtx,
) -> DeptWorkflowOut:
    return svc.resume_department_workflow(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
    )


@router.post(
    "/workflows/{workflow_id}/run-now",
    response_model=DeptWorkflowRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_now(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tc: TenantCtx,
    payload: DeptWorkflowRunNowRequest | None = None,
) -> DeptWorkflowRunOut:
    return svc.run_department_workflow_now(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
        payload=payload or DeptWorkflowRunNowRequest(),
    )


@router.get("/workflows/{workflow_id}/runs", response_model=list[DeptWorkflowRunOut])
def list_workflow_runs(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tc: TenantCtx,
) -> list[DeptWorkflowRunOut]:
    return svc.list_department_workflow_runs(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
    )


@router.get("/runs/{run_id}", response_model=DeptWorkflowRunDetailOut)
def get_run_detail(
    department_id: uuid.UUID,
    run_id: uuid.UUID,
    tc: TenantCtx,
) -> DeptWorkflowRunDetailOut:
    return svc.get_department_run_detail(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        run_id=run_id,
    )


class DeptApprovalOut(BaseModel):
    id: uuid.UUID
    department_id: uuid.UUID | None = None
    run_id: uuid.UUID
    node_id: str
    required_role: str
    status: str
    comment: str | None = None
    payload: dict[str, Any]
    expires_at: datetime | None = None
    created_at: datetime


class DeptApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    comment: str | None = None


@router.get("/approvals", response_model=list[DeptApprovalOut])
def list_department_approvals(
    department_id: uuid.UUID,
    tc: TenantCtx,
    status_filter: str = Query("pending"),
) -> list[DeptApprovalOut]:
    svc._require_dept_role(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        allowed_roles={Role.OWNER, Role.ADMIN, Role.REVIEWER},
    )
    stmt = (
        select(WorkflowApproval)
        .where(
            WorkflowApproval.tenant_id == tc.tenant_id,
            WorkflowApproval.department_id == department_id,
        )
        .order_by(WorkflowApproval.created_at.desc())
    )
    if status_filter != "all":
        stmt = stmt.where(WorkflowApproval.status == WorkflowApprovalStatus(status_filter))
    rows = tc.db.scalars(stmt).all()
    return [
        DeptApprovalOut(
            id=r.id,
            department_id=r.department_id,
            run_id=r.run_id,
            node_id=r.node_id,
            required_role=r.required_role,
            status=r.status.value,
            comment=r.comment,
            payload=r.payload or {},
            expires_at=r.expires_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/approvals/{approval_id}/decide", response_model=DeptApprovalOut)
def decide_department_approval_endpoint(
    department_id: uuid.UUID,
    approval_id: uuid.UUID,
    payload: DeptApprovalDecisionRequest,
    tc: TenantCtx,
) -> DeptApprovalOut:
    app = decide_workflow_approval(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        approval_id=approval_id,
        decision=payload.decision,
        comment=payload.comment,
    )
    return DeptApprovalOut(
        id=app.id,
        department_id=app.department_id,
        run_id=app.run_id,
        node_id=app.node_id,
        required_role=app.required_role,
        status=app.status.value,
        comment=app.comment,
        payload=app.payload or {},
        expires_at=app.expires_at,
        created_at=app.created_at,
    )


class TemplateConnectorReadiness(BaseModel):
    provider: str
    connected: bool


class DeptWorkflowTemplateOut(BaseModel):
    id: uuid.UUID
    key: str
    department_type: str
    name: str
    description: str | None = None
    required_connectors: list[str]
    connector_status: list[TemplateConnectorReadiness]
    ready: bool
    definition: dict[str, Any]


@global_templates_router.get("/department-workflows", response_model=list[DeptWorkflowOut])
def list_all_tenant_department_workflows(
    tc: TenantCtx,
) -> list[DeptWorkflowOut]:
    return svc.list_all_department_workflows(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
    )


@global_templates_router.get("/workflow-templates", response_model=list[DeptWorkflowTemplateOut])
def list_all_workflow_templates(
    user: CurrentUser,
    tc: TenantCtx,
) -> list[DeptWorkflowTemplateOut]:
    del user
    templates = tc.db.scalars(
        select(WorkflowTemplate)
        .where(WorkflowTemplate.is_active.is_(True))
        .order_by(WorkflowTemplate.department_type.asc(), WorkflowTemplate.name.asc())
    ).all()
    return [
        DeptWorkflowTemplateOut(
            id=tpl.id,
            key=tpl.key,
            department_type=tpl.department_type,
            name=tpl.name,
            description=tpl.description,
            required_connectors=[str(c).lower() for c in (tpl.required_connectors or [])],
            connector_status=[],
            ready=False,
            definition=tpl.definition or {},
        )
        for tpl in templates
    ]


@router.get("/workflow-templates", response_model=list[DeptWorkflowTemplateOut])
def list_department_workflow_templates(
    department_id: uuid.UUID,
    tc: TenantCtx,
    include_all: bool = False,
) -> list[DeptWorkflowTemplateOut]:
    dept, _ = svc._require_dept_role(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        allowed_roles=svc._READ_ROLES,
    )
    stmt = select(WorkflowTemplate).where(WorkflowTemplate.is_active.is_(True))
    if not include_all:
        stmt = stmt.where(WorkflowTemplate.department_type == dept.department_type.value)
    templates = tc.db.scalars(
        stmt.order_by(WorkflowTemplate.department_type.asc(), WorkflowTemplate.name.asc())
    ).all()

    connected_providers = {
        p.lower()
        for p in tc.db.scalars(
            select(Connection.provider).where(
                Connection.tenant_id == tc.tenant_id,
                Connection.department_id == department_id,
                Connection.status == ConnectionStatus.HEALTHY,
            )
        ).all()
    }

    out: list[DeptWorkflowTemplateOut] = []
    for tpl in templates:
        req = [str(c).lower() for c in (tpl.required_connectors or [])]
        statuses = [
            TemplateConnectorReadiness(provider=p, connected=(p in connected_providers))
            for p in req
        ]
        out.append(
            DeptWorkflowTemplateOut(
                id=tpl.id,
                key=tpl.key,
                department_type=tpl.department_type,
                name=tpl.name,
                description=tpl.description,
                required_connectors=req,
                connector_status=statuses,
                ready=all(s.connected for s in statuses),
                definition=tpl.definition or {},
            )
        )
    return out


@router.post(
    "/workflow-templates/{key}/use",
    response_model=DeptWorkflowOut,
    status_code=status.HTTP_201_CREATED,
)
def use_department_workflow_template(
    department_id: uuid.UUID,
    key: str,
    tc: TenantCtx,
) -> DeptWorkflowOut:
    svc._require_dept_role(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        allowed_roles=svc._WRITE_ROLES,
        require_active_dept=True,
    )
    tpl = tc.db.scalar(
        select(WorkflowTemplate).where(
            WorkflowTemplate.key == key,
            WorkflowTemplate.is_active.is_(True),
        )
    )
    if tpl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Workflow template '{key}' not found")

    candidate_name = tpl.name
    existing_name = tc.db.scalar(
        select(Workflow.id).where(
            Workflow.tenant_id == tc.tenant_id,
            Workflow.name == candidate_name,
        )
    )
    if existing_name is not None:
        candidate_name = f"{tpl.name} ({uuid.uuid4().hex[:4]})"

    return svc.create_department_workflow(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        payload=DeptWorkflowCreate(
            name=candidate_name,
            description=tpl.description,
            definition=WorkflowDefinition.model_validate(tpl.definition),
            template_key=tpl.key,
        ),
    )


@router.post("/workflows/{workflow_id}/eval")
def evaluate_department_workflow_endpoint(
    department_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tc: TenantCtx,
) -> dict[str, Any]:
    return svc.run_workflow_evaluation(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        workflow_id=workflow_id,
    )


@router.post("/runs/{run_id}/replay", response_model=DeptWorkflowRunDetailOut, status_code=status.HTTP_201_CREATED)
def replay_department_run_endpoint(
    department_id: uuid.UUID,
    run_id: uuid.UUID,
    tc: TenantCtx,
) -> DeptWorkflowRunDetailOut:
    from app.core.permissions import Permission, has_permission

    if not has_permission(tc.role, Permission.RUNS_REPLAY):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: runs.replay")
    run = replay_department_run(
        tc.db,
        tenant_id=tc.tenant_id,
        department_id=department_id,
        run_id=run_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
    )
    return svc.get_department_run_detail(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        run_id=run.id,
    )
