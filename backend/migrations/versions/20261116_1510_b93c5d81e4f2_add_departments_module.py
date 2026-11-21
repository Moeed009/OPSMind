"""add departments module tables, department_id foreign keys, and RLS policies

Revision ID: b93c5d81e4f2
Revises: a81b4c92d3e0
Create Date: 2026-11-16 15:10:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b93c5d81e4f2"
down_revision: str | None = "a81b4c92d3e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("template_key", sa.String(length=50), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "inactive",
                "archived",
                name="department_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("monthly_ai_cost_cap_usd", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "monthly_ai_cost_cap_usd IS NULL OR monthly_ai_cost_cap_usd >= 0",
            name=op.f("ck_departments_dept_cost_cap_non_negative"),
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_departments_created_by_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_departments_tenant_id_tenants"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_departments_tenant_id_id")),
        sa.UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_id_code"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_departments_tenant_id_name"),
    )
    op.create_index(op.f("ix_departments_tenant_id"), "departments", ["tenant_id"], unique=False)
    op.create_index("ix_departments_tenant_id_status", "departments", ["tenant_id", "status"], unique=False)

    op.create_table(
        "department_members",
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], name=op.f("fk_department_members_assigned_by_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_department_members_user_id_users"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name=op.f("fk_department_members_tenant_id_department_id_departments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_department_members_tenant_id_tenants"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_department_members")),
        sa.UniqueConstraint("tenant_id", "department_id", "user_id", name="uq_department_members_tenant_dept_user"),
    )
    op.create_index(op.f("ix_department_members_tenant_id"), "department_members", ["tenant_id"], unique=False)
    op.create_index("ix_department_members_tenant_id_user_id", "department_members", ["tenant_id", "user_id"], unique=False)
    op.create_index("ix_department_members_tenant_id_department_id", "department_members", ["tenant_id", "department_id"], unique=False)

    # Extend existing tables with nullable department_id and composite tenant FKs
    for tbl in ("connections", "workflows", "events", "runs", "usage_records"):
        op.add_column(tbl, sa.Column("department_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_{tbl}_tenant_id_department_id_departments"),
            tbl,
            "departments",
            ["tenant_id", "department_id"],
            ["tenant_id", "id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{tbl}_tenant_id_department_id", tbl, ["tenant_id", "department_id"], unique=False)

    # Update workflow_versions status check constraint to allow 'paused' and 'failed'
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
        "status IN ('draft', 'testing', 'gate_passed', 'published', 'paused', 'failed', 'archived')",
    )

    # Apply RLS and least-privilege grants to departments and department_members
    tenant = "(SELECT app_tenant_id())"
    for t in ("departments", "department_members"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO opsmind_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON department_members")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON departments")

    for tbl in ("usage_records", "runs", "events", "workflows", "connections"):
        op.drop_index(f"ix_{tbl}_tenant_id_department_id", table_name=tbl)
        op.drop_constraint(op.f(f"fk_{tbl}_tenant_id_department_id_departments"), tbl, type_="foreignkey")
        op.drop_column(tbl, "department_id")

    op.drop_index("ix_department_members_tenant_id_department_id", table_name="department_members")
    op.drop_index("ix_department_members_tenant_id_user_id", table_name="department_members")
    op.drop_index(op.f("ix_department_members_tenant_id"), table_name="department_members")
    op.drop_table("department_members")

    op.drop_index("ix_departments_tenant_id_status", table_name="departments")
    op.drop_index(op.f("ix_departments_tenant_id"), table_name="departments")
    op.drop_table("departments")
