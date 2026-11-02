import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import ReviewTaskStatus
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class ReviewTask(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        tenant_fk("run_id", "runs", ondelete="CASCADE"),
        tenant_fk("run_step_id", "run_steps"),
        Index(
            "uq_review_tasks_tenant_id_run_step_id_open",
            "tenant_id",
            "run_step_id",
            unique=True,
            postgresql_where=text("status IN ('open', 'escalated', 'claimed')"),
        ),
        Index("ix_review_tasks_inbox", "tenant_id", "status", "deadline_at"),
        Index("ix_review_tasks_tenant_id_run_id", "tenant_id", "run_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    run_step_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[ReviewTaskStatus] = mapped_column(
        enum_column(ReviewTaskStatus, "review_task_status"),
        default=ReviewTaskStatus.OPEN,
        server_default=ReviewTaskStatus.OPEN.value,
    )
    risk_reason: Mapped[str] = mapped_column(String(100))
    proposal: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    evidence: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    correction: Mapped[dict | None] = mapped_column(JSONB)