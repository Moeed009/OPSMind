from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import Plan, SubscriptionStatus
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        Index(
            "uq_subscriptions_tenant_id_live",
            "tenant_id",
            unique=True,
            postgresql_where=text("status IN ('incomplete', 'active', 'past_due')"),
        ),
    )

    plan: Mapped[Plan] = mapped_column(enum_column(Plan, "subscription_plan"))
    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_column(SubscriptionStatus, "subscription_status")
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )