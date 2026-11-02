import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import ActionStatus
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class ActionLog(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "action_logs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        tenant_fk("run_id", "runs", ondelete="CASCADE"),
        tenant_fk("run_step_id", "run_steps"),
        tenant_fk("connection_id", "connections"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_action_logs_tenant_id_run_id", "tenant_id", "run_id"),
        Index(
            "ix_action_logs_unresolved",
            "tenant_id",
            "status",
            postgresql_where=text("status IN ('pending', 'unknown')"),
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    run_step_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    connection_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action_type: Mapped[str] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[ActionStatus] = mapped_column(
        enum_column(ActionStatus, "action_status"),
        default=ActionStatus.PENDING,
        server_default=ActionStatus.PENDING.value,
    )
    request: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    response: Mapped[dict | None] = mapped_column(JSONB)
    external_ref: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))