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
from app.db.enums import RunStepStatus
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class RunStep(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("run_id", "step_index", "attempt"),
        tenant_fk("run_id", "runs", ondelete="CASCADE"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        Index("ix_run_steps_tenant_id_run_id", "tenant_id", "run_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    step_index: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    node_id: Mapped[str] = mapped_column(String(100))
    step_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[RunStepStatus] = mapped_column(
        enum_column(RunStepStatus, "run_step_status"),
        default=RunStepStatus.RUNNING,
        server_default=RunStepStatus.RUNNING.value,
    )
    input: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    output: Mapped[dict | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(100))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    prompt_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))