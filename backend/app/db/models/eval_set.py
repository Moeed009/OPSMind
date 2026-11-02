import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Integer, Numeric, String, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class EvalSet(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "eval_sets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "name"),
        tenant_fk("workflow_id", "workflows"),
        CheckConstraint("pass_threshold >= 0 AND pass_threshold <= 1", name="threshold_range"),
        CheckConstraint("case_count >= 0", name="case_count_non_negative"),
    )

    workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(200))
    job_type: Mapped[str] = mapped_column(String(50))
    cases: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    case_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    pass_threshold: Mapped[Decimal] = mapped_column(Numeric(5, 4), server_default=text("0.8000"))