import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import UsageMetric
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class UsageRecord(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_key"),
        tenant_fk("run_id", "runs"),
        tenant_fk("workflow_id", "workflows"),
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        CheckConstraint("cost_usd >= 0", name="cost_non_negative"),
                Index(
            "ix_usage_records_tenant_id_metric_occurred_at",
            "tenant_id",
            "metric",
            "occurred_at",
        ),
        Index("ix_usage_records_tenant_id_run_id", "tenant_id", "run_id"),
    )

    metric: Mapped[UsageMetric] = mapped_column(enum_column(UsageMetric, "usage_metric"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    source_key: Mapped[str] = mapped_column(String(255))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )