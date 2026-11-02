from decimal import Decimal

from sqlalchemy import CheckConstraint, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import Plan, Region
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'", name="slug_format"),
    )

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    region: Mapped[Region] = mapped_column(enum_column(Region, "region"))
    plan: Mapped[Plan] = mapped_column(
        enum_column(Plan, "plan"), default=Plan.FREE, server_default=Plan.FREE.value
    )
    monthly_ai_cost_cap_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), server_default=text("5.0000")
    )