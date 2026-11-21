"""WorkflowApproval model for department workflow human approval gates."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import WorkflowApprovalStatus
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class WorkflowApproval(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "workflow_approvals"
    __table_args__ = (
        tenant_fk("department_id", "departments"),
        tenant_fk("run_id", "runs", ondelete="CASCADE"),
        Index("ix_workflow_approvals_tenant_dept_status", "tenant_id", "department_id", "status"),
        Index("ix_workflow_approvals_tenant_run_id", "tenant_id", "run_id"),
    )

    department_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    node_id: Mapped[str] = mapped_column(String(64))
    required_role: Mapped[str] = mapped_column(String(32), default="reviewer", server_default="reviewer")
    status: Mapped[WorkflowApprovalStatus] = mapped_column(
        enum_column(WorkflowApprovalStatus, "workflow_approval_status"),
        default=WorkflowApprovalStatus.PENDING,
        server_default=WorkflowApprovalStatus.PENDING.value,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
