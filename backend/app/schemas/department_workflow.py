"""Pydantic schemas for department-scoped workflows, lifecycle, and runs."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.workflows.definition import WorkflowDefinition

DeptWorkflowStatus = Literal["draft", "in_review", "active", "paused"]


class DeptWorkflowCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    definition: WorkflowDefinition
    template_key: str | None = Field(default=None, max_length=100)


class DeptWorkflowUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    definition: WorkflowDefinition | None = None


class DeptWorkflowReviewDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    decision: Literal["approve", "send_back"]
    comment: str | None = None


class DeptWorkflowRunNowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict)


class DeptWorkflowOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    department_id: uuid.UUID
    name: str
    description: str | None = None
    status: DeptWorkflowStatus
    definition: dict[str, Any]
    version: int
    active_version: int | None = None
    created_by: uuid.UUID | None = None
    template_key: str | None = None
    review_comment: str | None = None
    pause_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class DeptWorkflowRunStepOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    node_id: str
    node_type: str
    status: str
    input_preview: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class DeptWorkflowRunOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    department_id: uuid.UUID | None = None
    workflow_id: uuid.UUID | None = None
    status: str
    trigger_type: str
    scheduled_for: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    created_at: datetime


class DeptWorkflowRunDetailOut(DeptWorkflowRunOut):
    steps: list[DeptWorkflowRunStepOut] = Field(default_factory=list)
