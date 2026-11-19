"""AIRun SQLAlchemy model for department AI execution observability."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, UUIDPrimaryKeyMixin


class AIRun(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        tenant_fk("department_id", "departments"),
        tenant_fk("workflow_run_id", "runs", ondelete="SET NULL"),
        Index("ix_ai_runs_tenant_dept_created", "tenant_id", "department_id", "created_at"),
    )

    department_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    task: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
