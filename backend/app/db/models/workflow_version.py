import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
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
from app.db.enums import WorkflowVersionStatus
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class WorkflowVersion(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("workflow_id", "version_number"),
        tenant_fk("workflow_id", "workflows", ondelete="CASCADE"),
        tenant_fk("trigger_connection_id", "connections"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint(
            "eval_score IS NULL OR (eval_score >= 0 AND eval_score <= 1)",
            name="eval_score_range",
        ),
        Index(
            "uq_workflow_versions_workflow_id_published",
            "workflow_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index(
            "ix_workflow_versions_trigger",
            "tenant_id",
            "trigger_connection_id",
            "trigger_event_type",
            postgresql_where=text("status = 'published'"),
        ),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version_number: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[WorkflowVersionStatus] = mapped_column(
        enum_column(WorkflowVersionStatus, "workflow_version_status"),
        default=WorkflowVersionStatus.DRAFT,
        server_default=WorkflowVersionStatus.DRAFT.value,
    )
    trigger_connection_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    trigger_event_type: Mapped[str | None] = mapped_column(String(100))
    eval_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))