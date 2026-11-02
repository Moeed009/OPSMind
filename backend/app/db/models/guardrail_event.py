import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import GuardrailDecision
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class GuardrailEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "guardrail_events"
    __table_args__ = (
        tenant_fk("run_id", "runs"),
        tenant_fk("run_step_id", "run_steps"),
        Index("ix_guardrail_events_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_guardrail_events_tenant_id_run_id", "tenant_id", "run_id"),
    )

    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    run_step_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    layer: Mapped[str] = mapped_column(String(30))
    rule_id: Mapped[str] = mapped_column(String(100))
    decision: Mapped[GuardrailDecision] = mapped_column(
        enum_column(GuardrailDecision, "guardrail_decision")
    )
    reason: Mapped[str] = mapped_column(Text)
    excerpt_redacted: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )