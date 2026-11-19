"""Pydantic schemas for Department Management, Members, Connections, Workflows, and Dashboard."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.enums import DepartmentRole, DepartmentStatus, DepartmentType, Role

CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}[a-z0-9]$|^[a-z0-9]{1,2}$")


def _validate_iana_timezone(tz: str) -> str:
    cleaned = tz.strip()
    if not cleaned:
        raise ValueError("Timezone cannot be empty.")
    try:
        ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValueError(f"Invalid IANA timezone: '{cleaned}'") from exc
    return cleaned


class DepartmentTemplateOut(BaseModel):
    key: str
    name: str
    code: str
    description: str
    recommended_providers: list[str] = Field(default_factory=list)
    default_settings: dict[str, Any] = Field(default_factory=dict)


class DepartmentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=50)
    description: str | None = None
    template_key: str | None = Field(default=None, max_length=50)
    department_type: DepartmentType | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    monthly_ai_cost_cap_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    timezone: str = Field(default="Asia/Karachi", max_length=64)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        return _validate_iana_timezone(v)

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        cleaned = re.sub(r"[^a-z0-9_-]+", "-", v.strip().lower()).strip("-")
        if not cleaned or not CODE_PATTERN.match(cleaned):
            raise ValueError("Department code must contain only lowercase letters, numbers, hyphens, or underscores.")
        return cleaned


class DepartmentUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    department_type: DepartmentType | None = None
    settings: dict[str, Any] | None = None
    monthly_ai_cost_cap_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    clear_cost_cap: bool = False
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _validate_iana_timezone(v)


class DepartmentMemberAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[uuid.UUID] = Field(min_length=1)
    role: DepartmentRole | None = None


class DepartmentMemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: DepartmentRole


class DepartmentMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str | None = None
    role: Role
    department_role: DepartmentRole | None = None
    effective_role: Role
    assigned_at: datetime


class DepartmentOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    code: str
    description: str | None = None
    template_key: str | None = None
    department_type: DepartmentType = DepartmentType.OPERATIONS
    status: DepartmentStatus
    settings: dict[str, Any] = Field(default_factory=dict)
    monthly_ai_cost_cap_usd: Decimal | None = None
    timezone: str = "Asia/Karachi"
    member_count: int = 0
    connection_count: int = 0
    workflow_count: int = 0
    active_workflow_count: int = 0
    my_effective_role: Role | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class DepartmentConnectionItem(BaseModel):
    id: uuid.UUID
    provider: str
    display_name: str
    external_account_id: str | None = None
    status: str
    department_id: uuid.UUID | None = None
    last_error: str | None = None
    last_synced_at: datetime | None = None


class DepartmentWorkflowItem(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    department_id: uuid.UUID | None = None
    published_version_id: uuid.UUID | None = None
    latest_version_number: int = 1
    latest_version_status: str = "draft"
    pause_reason: str | None = None
    updated_at: datetime
    archived_at: datetime | None = None


class DepartmentRunItem(BaseModel):
    id: uuid.UUID
    workflow_version_id: uuid.UUID
    workflow_name: str | None = None
    trigger_source: str
    status: str
    trace_id: str
    total_tokens: int = 0
    cost_usd: Decimal = Decimal("0.000000")
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DepartmentOperationalMetrics(BaseModel):
    total_members: int = 0
    total_connections: int = 0
    healthy_connections: int = 0
    total_workflows: int = 0
    published_workflows: int = 0
    paused_workflows: int = 0
    draft_workflows: int = 0
    total_runs: int = 0
    succeeded_runs: int = 0
    failed_runs: int = 0
    waiting_review_runs: int = 0
    ai_requests_count: int = 0
    ai_prompt_tokens: int = 0
    ai_completion_tokens: int = 0
    ai_total_tokens: int = 0
    ai_cost_usd: Decimal = Decimal("0.000000")
    department_monthly_cap_usd: Decimal | None = None
    company_monthly_cap_usd: Decimal = Decimal("5.0000")


class DepartmentDashboardOut(BaseModel):
    department: DepartmentOut
    members: list[DepartmentMemberOut] = Field(default_factory=list)
    connections: list[DepartmentConnectionItem] = Field(default_factory=list)
    workflows: list[DepartmentWorkflowItem] = Field(default_factory=list)
    recent_runs: list[DepartmentRunItem] = Field(default_factory=list)
    metrics: DepartmentOperationalMetrics
