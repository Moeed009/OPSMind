"""add storage_path/gmail metadata to hr_candidate_documents and hr_ingestion_exceptions table

Revision ID: e2f7a0b9c6d1
Revises: d1e6f9a8b5c0
Create Date: 2026-11-18 15:25:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f7a0b9c6d1"
down_revision: str | None = "d1e6f9a8b5c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("hr_candidate_documents", sa.Column("storage_path", sa.String(length=500), nullable=True))
    op.add_column("hr_candidate_documents", sa.Column("gmail_message_id", sa.String(length=255), nullable=True))
    op.add_column("hr_candidate_documents", sa.Column("event_id", sa.Uuid(), nullable=True))

    op.create_table(
        "hr_ingestion_exceptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=True),
        sa.Column("sender_email", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="open", nullable=False),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name="fk_hr_ing_exc_tenant_dept",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["connections.tenant_id", "connections.id"],
            name="fk_hr_ing_exc_tenant_conn",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            ["events.tenant_id", "events.id"],
            name="fk_hr_ing_exc_tenant_event",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_hr_ingestion_exceptions_tenant_id_id"),
    )
    op.create_index("ix_hr_ingestion_exceptions_tenant_id", "hr_ingestion_exceptions", ["tenant_id"])
    op.create_index(
        "ix_hr_ing_exc_tenant_dept_status",
        "hr_ingestion_exceptions",
        ["tenant_id", "department_id", "status"],
    )

    tenant = "(SELECT app_tenant_id())"
    op.execute("ALTER TABLE hr_ingestion_exceptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hr_ingestion_exceptions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON hr_ingestion_exceptions "
        f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON hr_ingestion_exceptions TO opsmind_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON hr_ingestion_exceptions")
    op.drop_index("ix_hr_ing_exc_tenant_dept_status", table_name="hr_ingestion_exceptions")
    op.drop_index("ix_hr_ingestion_exceptions_tenant_id", table_name="hr_ingestion_exceptions")
    op.drop_table("hr_ingestion_exceptions")
    op.drop_column("hr_candidate_documents", "event_id")
    op.drop_column("hr_candidate_documents", "gmail_message_id")
    op.drop_column("hr_candidate_documents", "storage_path")
