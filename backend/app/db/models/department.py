import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import DepartmentRole, DepartmentStatus, DepartmentType
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class Department(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_id_code"),
        UniqueConstraint("tenant_id", "name", name="uq_departments_tenant_id_name"),
        CheckConstraint(
            "monthly_ai_cost_cap_usd IS NULL OR monthly_ai_cost_cap_usd >= 0",
            name="dept_cost_cap_non_negative",
        ),
        Index("ix_departments_tenant_id_status", "tenant_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    template_key: Mapped[str | None] = mapped_column(String(50))
    department_type: Mapped[DepartmentType] = mapped_column(
        enum_column(DepartmentType, "department_type"),
        default=DepartmentType.OPERATIONS,
        server_default=DepartmentType.OPERATIONS.value,
    )
    status: Mapped[DepartmentStatus] = mapped_column(
        enum_column(DepartmentStatus, "department_status"),
        default=DepartmentStatus.ACTIVE,
        server_default=DepartmentStatus.ACTIVE.value,
    )
    settings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    monthly_ai_cost_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Karachi", server_default="Asia/Karachi"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DepartmentMember(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "department_members"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "department_id",
            "user_id",
            name="uq_department_members_tenant_dept_user",
        ),
        tenant_fk("department_id", "departments", ondelete="CASCADE"),
        Index("ix_department_members_tenant_id_user_id", "tenant_id", "user_id"),
        Index("ix_department_members_tenant_id_department_id", "tenant_id", "department_id"),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    role: Mapped[DepartmentRole | None] = mapped_column(
        enum_column(DepartmentRole, "department_role"), nullable=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
