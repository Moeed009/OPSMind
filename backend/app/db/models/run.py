import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import RunStatus, TriggerSource
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class Run(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        tenant_fk("workflow_version_id", "workflow_versions"),
        tenant_fk("event_id", "events"),
        CheckConstraint("cost_usd >= 0", name="cost_non_negative"),
        Index("ix_runs_tenant_id_workflow_version_id", "tenant_id", "workflow_version_id"),
        Index("ix_runs_tenant_id_created_at", "tenant_id", "created_at"),
        Index(
            "ix_runs_active",
            "tenant_id",
            "status",
            postgresql_where=text(
                "status IN ('queued', 'running', 'waiting_timer', 'waiting_human', 'retrying')"
            ),
        ),
    )

    workflow_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    trigger_source: Mapped[TriggerSource] = mapped_column(
        enum_column(TriggerSource, "trigger_source")
    )
    status: Mapped[RunStatus] = mapped_column(
        enum_column(RunStatus, "run_status"),
        default=RunStatus.QUEUED,
        server_default=RunStatus.QUEUED.value,
    )
    trace_id: Mapped[str] = mapped_column(String(32), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    worker_task_id: Mapped[str | None] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    output: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))