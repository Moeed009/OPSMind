import uuid
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Index, Numeric, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class EvalResult(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "eval_results"
    __table_args__ = (
        tenant_fk("eval_set_id", "eval_sets", ondelete="CASCADE"),
        tenant_fk("workflow_version_id", "workflow_versions"),
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        CheckConstraint("cost_usd >= 0", name="cost_non_negative"),
        Index("ix_eval_results_tenant_id_eval_set_id_created_at", "tenant_id", "eval_set_id", "created_at"),
    )

    eval_set_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    passed: Mapped[bool] = mapped_column(Boolean)
    model: Mapped[str | None] = mapped_column(String(100))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    case_results: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))