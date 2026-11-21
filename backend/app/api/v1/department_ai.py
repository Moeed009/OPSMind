from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.ai.exceptions import AIError
from app.ai.rate_limit import RedisRateLimiter
from app.ai.services.dept_ai_service import AiContext, DepartmentAiService
from app.api.deps import CurrentUser, TenantCtx
from app.core.config import get_settings
from app.db.enums import Role
from app.db.models.ai_run import AIRun
from app.services.department_workflows import _require_dept_role
from app.workflows.definition import AINode

router = APIRouter(tags=["department-ai"])

_user_test_limiter = RedisRateLimiter()


class AiTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: Literal["classify", "summarize", "draft_reply", "extract", "score"]
    input: Any
    prompt: str | None = None
    labels: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    confidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    provider: Literal["auto", "groq", "gemini"] = "auto"
    model: str | None = None


class AiTestResponse(BaseModel):
    status: str
    output: dict[str, Any]
    confidence: float
    provider: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int


class AiRunItemOut(BaseModel):
    id: uuid.UUID
    department_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    node_id: str | None = None
    provider: str
    model: str
    task: str
    status: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    error_code: str | None = None
    input_preview: str | None = None
    created_at: datetime


class AiRunsPageOut(BaseModel):
    items: list[AiRunItemOut]
    total: int
    limit: int
    offset: int


class AiProviderStatusOut(BaseModel):
    groq: bool
    gemini: bool
    primary: str
    fallback: str


@router.get("/ai/providers/status", response_model=AiProviderStatusOut)
def get_ai_providers_status(user: CurrentUser) -> AiProviderStatusOut:
    del user
    settings = get_settings()
    return AiProviderStatusOut(
        groq=bool(settings.groq_api_key and settings.groq_api_key.strip()),
        gemini=bool(settings.gemini_api_key and settings.gemini_api_key.strip()),
        primary=settings.ai_primary_provider,
        fallback=settings.ai_fallback_provider,
    )


@router.post("/departments/{department_id}/ai/test", response_model=AiTestResponse)
async def test_department_ai(
    department_id: uuid.UUID,
    payload: AiTestRequest,
    tc: TenantCtx,
) -> AiTestResponse:
    _require_dept_role(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        allowed_roles={Role.OWNER, Role.ADMIN, Role.BUILDER},
        require_active_dept=True,
    )
    try:
        _user_test_limiter.acquire(f"user_test:{tc.user.id}", 20)
    except AIError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc

    node = AINode(
        id="test_node",
        task=payload.task,
        prompt=payload.prompt,
        labels=payload.labels,
        output_schema=payload.output_schema,
        confidence_threshold=payload.confidence_threshold,
        provider=payload.provider,
        model=payload.model,
    )
    service = DepartmentAiService()
    try:
        res = await service.run(
            tc.db,
            AiContext(tenant_id=tc.tenant_id, department_id=department_id, node_id="test_node"),
            payload.task,
            payload.input,
            node,
        )
    except AIError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return AiTestResponse(
        status=res.status,
        output=res.output,
        confidence=res.confidence,
        provider=res.provider,
        model=res.model,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
    )


@router.get("/departments/{department_id}/ai/runs", response_model=AiRunsPageOut)
def list_department_ai_runs(
    department_id: uuid.UUID,
    tc: TenantCtx,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> AiRunsPageOut:
    _require_dept_role(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        tenant_role=tc.role,
        department_id=department_id,
        allowed_roles={Role.OWNER, Role.ADMIN},
    )
    total = int(
        tc.db.scalar(
            select(func.count(AIRun.id)).where(
                AIRun.tenant_id == tc.tenant_id,
                AIRun.department_id == department_id,
            )
        )
        or 0
    )
    rows = tc.db.scalars(
        select(AIRun)
        .where(
            AIRun.tenant_id == tc.tenant_id,
            AIRun.department_id == department_id,
        )
        .order_by(AIRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return AiRunsPageOut(
        items=[
            AiRunItemOut(
                id=r.id,
                department_id=r.department_id,
                workflow_run_id=r.workflow_run_id,
                node_id=r.node_id,
                provider=r.provider,
                model=r.model,
                task=r.task,
                status=r.status,
                latency_ms=r.latency_ms,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                error_code=r.error_code,
                input_preview=r.input_preview,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
