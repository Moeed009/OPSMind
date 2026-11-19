"""Pydantic schemas for Runs and Run Steps."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.db.enums import RunStatus, RunStepStatus, TriggerSource


class RunTriggerRequest(BaseModel):
    input_data: dict[str, Any] = Field(default_factory=dict)

class RunStepOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    step_index: int
    attempt: int
    node_id: str
    step_type: str
    status: RunStepStatus
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    model: str | None = None
    confidence: Decimal | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal("0.000000")
    latency_ms: int | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

class RunOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    department_id: uuid.UUID | None = None
    workflow_version_id: uuid.UUID
    trigger_source: TriggerSource
    status: RunStatus
    trace_id: str
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    error: str | None = None
    cost_usd: Decimal = Decimal("0.000000")
    total_tokens: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[RunStepOut] = Field(default_factory=list)
