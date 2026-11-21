"""add ai_runs table with tenant isolation and RLS

Revision ID: f7a2b5c4d1e6
Revises: e6f1a4b3c0d5
Create Date: 2026-11-17 12:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a2b5c4d1e6"
down_revision: str | None = "e6f1a4b3c0d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("node_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("input_preview", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name="fk_ai_runs_tenant_id_department_id_departments",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_run_id"],
            ["runs.tenant_id", "runs.id"],
            name="fk_ai_runs_tenant_id_workflow_run_id_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_runs_tenant_id", "ai_runs", ["tenant_id"])
    op.create_index(
        "ix_ai_runs_tenant_dept_created",
        "ai_runs",
        ["tenant_id", "department_id", "created_at"],
    )

    tenant = "(SELECT app_tenant_id())"
    op.execute("ALTER TABLE ai_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON ai_runs "
        f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ai_runs TO opsmind_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON ai_runs")
    op.drop_index("ix_ai_runs_tenant_dept_created", table_name="ai_runs")
    op.drop_index("ix_ai_runs_tenant_id", table_name="ai_runs")
    op.drop_table("ai_runs")
