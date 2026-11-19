"""extend workflows/runs for dept workflow lifecycle and add workflow_approvals

Revision ID: e6f1a4b3c0d5
Revises: d5e0f3a2b9c4
Create Date: 2026-11-17 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6f1a4b3c0d5"
down_revision: str | None = "d5e0f3a2b9c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("template_key", sa.String(length=100), nullable=True))
    op.add_column("workflows", sa.Column("review_comment", sa.Text(), nullable=True))

    op.execute(
        """
        DO $$
        DECLARE cname text;
        BEGIN
          SELECT con.conname INTO cname
          FROM pg_constraint con
          JOIN pg_class rel ON rel.oid = con.conrelid
          WHERE rel.relname = 'workflow_versions'
            AND con.contype = 'c'
            AND pg_get_constraintdef(con.oid) LIKE '%status%';
          IF cname IS NOT NULL THEN
            EXECUTE 'ALTER TABLE workflow_versions DROP CONSTRAINT ' || quote_ident(cname);
          END IF;
        END $$;
        """
    )
    op.create_check_constraint(
        "ck_workflow_versions_workflow_version_status",
        "workflow_versions",
        "status IN ('draft', 'in_review', 'testing', 'gate_passed', 'published', 'paused', 'failed', 'archived')",
    )

    op.add_column("runs", sa.Column("workflow_id", sa.Uuid(), nullable=True))
    op.add_column("runs", sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_runs_tenant_id_workflow_id_workflows",
        "runs",
        "workflows",
        ["tenant_id", "workflow_id"],
        ["tenant_id", "id"],
    )
    op.create_index("ix_runs_tenant_id_workflow_id", "runs", ["tenant_id", "workflow_id"])
    op.create_index(
        "uq_runs_workflow_scheduled_for",
        "runs",
        ["workflow_id", "scheduled_for"],
        unique=True,
        postgresql_where=sa.text("scheduled_for IS NOT NULL"),
    )

    op.execute(
        """
        DO $$
        DECLARE cname text;
        BEGIN
          SELECT con.conname INTO cname
          FROM pg_constraint con
          JOIN pg_class rel ON rel.oid = con.conrelid
          WHERE rel.relname = 'runs'
            AND con.contype = 'c'
            AND pg_get_constraintdef(con.oid) LIKE '%status%';
          IF cname IS NOT NULL THEN
            EXECUTE 'ALTER TABLE runs DROP CONSTRAINT ' || quote_ident(cname);
          END IF;
        END $$;
        """
    )
    op.create_check_constraint(
        "ck_runs_run_status",
        "runs",
        "status IN ('queued', 'running', 'waiting_timer', 'waiting_human', 'waiting_approval', 'needs_review', 'retrying', 'completed', 'succeeded', 'failed', 'cancelled')",
    )

    op.create_table(
        "workflow_approvals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("required_role", sa.String(length=32), server_default="reviewer", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "expired",
                name="workflow_approval_status",
                native_enum=False,
                length=32,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name="fk_workflow_approvals_tenant_id_department_id_departments",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            name="fk_workflow_approvals_tenant_id_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_approvals_tenant_id", "workflow_approvals", ["tenant_id"])
    op.create_index(
        "ix_workflow_approvals_tenant_dept_status",
        "workflow_approvals",
        ["tenant_id", "department_id", "status"],
    )
    op.create_index(
        "ix_workflow_approvals_tenant_run_id",
        "workflow_approvals",
        ["tenant_id", "run_id"],
    )

    tenant = "(SELECT app_tenant_id())"
    op.execute("ALTER TABLE workflow_approvals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_approvals FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON workflow_approvals "
        f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_approvals TO opsmind_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON workflow_approvals")
    op.drop_index("ix_workflow_approvals_tenant_run_id", table_name="workflow_approvals")
    op.drop_index("ix_workflow_approvals_tenant_dept_status", table_name="workflow_approvals")
    op.drop_index("ix_workflow_approvals_tenant_id", table_name="workflow_approvals")
    op.drop_table("workflow_approvals")

    op.drop_constraint("ck_runs_run_status", "runs", type_="check")
    op.create_check_constraint(
        "ck_runs_run_status",
        "runs",
        "status IN ('queued', 'running', 'waiting_timer', 'waiting_human', 'retrying', 'completed', 'failed', 'cancelled')",
    )

    op.drop_index("uq_runs_workflow_scheduled_for", table_name="runs")
    op.drop_index("ix_runs_tenant_id_workflow_id", table_name="runs")
    op.drop_constraint("fk_runs_tenant_id_workflow_id_workflows", "runs", type_="foreignkey")
    op.drop_column("runs", "scheduled_for")
    op.drop_column("runs", "workflow_id")

    op.drop_constraint("ck_workflow_versions_workflow_version_status", "workflow_versions", type_="check")
    op.create_check_constraint(
        "ck_workflow_versions_workflow_version_status",
        "workflow_versions",
        "status IN ('draft', 'testing', 'gate_passed', 'published', 'paused', 'failed', 'archived')",
    )

    op.drop_column("workflows", "review_comment")
    op.drop_column("workflows", "template_key")
