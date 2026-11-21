"""add hr_jobs and hr_job_requirements tables with tenant isolation and RLS

Revision ID: b9c4d7e6f3a8
Revises: a8b3c6d5e2f7
Create Date: 2026-11-18 13:05:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9c4d7e6f3a8"
down_revision: str | None = "a8b3c6d5e2f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hr_jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("responsibilities", sa.Text(), nullable=True),
        sa.Column("education_requirements", sa.Text(), nullable=True),
        sa.Column("min_experience_years", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "employment_type",
            sa.Enum(
                "full_time",
                "part_time",
                "contract",
                "internship",
                name="employment_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="full_time",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "open",
                "closed",
                "archived",
                name="job_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("min_experience_years >= 0", name="hr_jobs_min_exp_non_negative"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name="fk_hr_jobs_tenant_id_department_id_departments",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_hr_jobs_tenant_id_id"),
    )
    op.create_index("ix_hr_jobs_tenant_id", "hr_jobs", ["tenant_id"])
    op.create_index(
        "ix_hr_jobs_tenant_dept_status",
        "hr_jobs",
        ["tenant_id", "department_id", "status"],
    )

    op.create_table(
        "hr_job_requirements",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "required_skill",
                "preferred_skill",
                "experience",
                "education",
                "qualification",
                name="requirement_category",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("is_mandatory", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("weight", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("weight >= 1 AND weight <= 10", name="hr_job_req_weight_range"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["hr_jobs.tenant_id", "hr_jobs.id"],
            name="fk_hr_job_requirements_tenant_id_job_id_hr_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_hr_job_requirements_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "job_id",
            "category",
            "name",
            name="uq_hr_job_req_tenant_job_cat_name",
        ),
    )
    op.create_index("ix_hr_job_requirements_tenant_id", "hr_job_requirements", ["tenant_id"])
    op.create_index("ix_hr_job_req_tenant_job", "hr_job_requirements", ["tenant_id", "job_id"])

    tenant = "(SELECT app_tenant_id())"
    for tbl in ("hr_jobs", "hr_job_requirements"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {tbl} "
            f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO opsmind_app")


def downgrade() -> None:
    for tbl in ("hr_job_requirements", "hr_jobs"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
    op.drop_index("ix_hr_job_req_tenant_job", table_name="hr_job_requirements")
    op.drop_index("ix_hr_job_requirements_tenant_id", table_name="hr_job_requirements")
    op.drop_table("hr_job_requirements")
    op.drop_index("ix_hr_jobs_tenant_dept_status", table_name="hr_jobs")
    op.drop_index("ix_hr_jobs_tenant_id", table_name="hr_jobs")
    op.drop_table("hr_jobs")
