"""Pydantic schemas for Review Tasks and Human-in-the-Loop Decisions."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.db.enums import ReviewTaskStatus


class ReviewTaskDecisionRequest(BaseModel):
    decision: str = Field(description="'approved' | 'edited' | 'rejected'")
    reason: str | None = None
    correction: dict[str, Any] | None = None

class ReviewTaskOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    run_step_id: uuid.UUID
    status: ReviewTaskStatus
    risk_reason: str
    proposal: dict[str, Any]
    evidence: dict[str, Any]
    confidence: Decimal | None = None
    deadline_at: datetime
    claimed_by: uuid.UUID | None = None
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    workflow_name: str | None = None
    department_id: uuid.UUID | None = None
    is_legacy_readonly: bool = False
