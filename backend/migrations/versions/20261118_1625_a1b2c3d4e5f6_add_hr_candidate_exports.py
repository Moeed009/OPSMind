"""add hr_candidate_exports table for Google Sheets export tracking and duplicate protection

Revision ID: a1b2c3d4e5f6
Revises: f3a8b1c0d7e2
Create Date: 2026-11-18 16:25:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f3a8b1c0d7e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hr_candidate_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=True),
        sa.Column("connection_id", sa.Uuid(), nullable=True),
        sa.Column("action_log_id", sa.Uuid(), nullable=True),
        sa.Column("spreadsheet_id", sa.String(length=255), nullable=False),
        sa.Column("worksheet_id", sa.String(length=128), nullable=True),
        sa.Column("worksheet_name", sa.String(length=255), server_default="Candidates", nullable=False),
        sa.Column("row_reference", sa.String(length=255), nullable=True),
        sa.Column("export_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint(
            "tenant_id",
            "application_id",
            "spreadsheet_id",
            "worksheet_name",
            name="uq_hr_export_tenant_app_sheet_ws",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["hr_candidates.tenant_id", "hr_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["hr_candidate_applications.tenant_id", "hr_candidate_applications.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "evaluation_id"],
            ["hr_candidate_evaluations.tenant_id", "hr_candidate_evaluations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["connections.tenant_id", "connections.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["action_log_id"], ["action_logs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_hr_export_tenant_app", "hr_candidate_exports", ["tenant_id", "application_id"])

    op.execute("ALTER TABLE hr_candidate_exports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hr_candidate_exports FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY hr_candidate_exports_tenant_isolation ON hr_candidate_exports
        USING (tenant_id = (SELECT app_tenant_id()))
        WITH CHECK (tenant_id = (SELECT app_tenant_id()))
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON hr_candidate_exports TO opsmind_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS hr_candidate_exports_tenant_isolation ON hr_candidate_exports")
    op.drop_index("ix_hr_export_tenant_app", table_name="hr_candidate_exports")
    op.drop_table("hr_candidate_exports")
