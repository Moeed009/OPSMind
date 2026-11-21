"""add hr_candidates, hr_candidate_applications, and hr_candidate_evaluations tables

Revision ID: c0d5e8f7a4b9
Revises: b9c4d7e6f3a8
Create Date: 2026-11-18 13:20:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0d5e8f7a4b9"
down_revision: str | None = "b9c4d7e6f3a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hr_candidates",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name="fk_hr_candidates_tenant_id_department_id_departments",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_hr_candidates_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "department_id",
            "email",
            name="uq_hr_candidates_tenant_dept_email",
        ),
    )
    op.create_index("ix_hr_candidates_tenant_id", "hr_candidates", ["tenant_id"])
    op.create_index("ix_hr_candidates_tenant_dept", "hr_candidates", ["tenant_id", "department_id"])

    op.create_table(
        "hr_candidate_applications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "application_status",
            sa.Enum(
                "applied",
                "screening",
                "shortlisted",
                "interview",
                "offered",
                "hired",
                "rejected",
                "withdrawn",
                name="application_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="applied",
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "email",
                "workflow",
                "referral",
                name="application_source",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name="fk_hr_app_tenant_dept",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["hr_candidates.tenant_id", "hr_candidates.id"],
            name="fk_hr_app_tenant_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["hr_jobs.tenant_id", "hr_jobs.id"],
            name="fk_hr_app_tenant_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_hr_candidate_applications_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "candidate_id",
            "job_id",
            name="uq_hr_app_tenant_cand_job",
        ),
    )
    op.create_index("ix_hr_candidate_applications_tenant_id", "hr_candidate_applications", ["tenant_id"])
    op.create_index(
        "ix_hr_app_tenant_dept_job",
        "hr_candidate_applications",
        ["tenant_id", "department_id", "job_id"],
    )
    op.create_index("ix_hr_app_tenant_cand", "hr_candidate_applications", ["tenant_id", "candidate_id"])

    op.create_table(
        "hr_candidate_evaluations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column(
            "evaluation_status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "needs_review",
                name="evaluation_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name="fk_hr_eval_tenant_dept",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["hr_candidate_applications.tenant_id", "hr_candidate_applications.id"],
            name="fk_hr_eval_tenant_app",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_hr_candidate_evaluations_tenant_id_id"),
    )
    op.create_index("ix_hr_candidate_evaluations_tenant_id", "hr_candidate_evaluations", ["tenant_id"])
    op.create_index("ix_hr_eval_tenant_app", "hr_candidate_evaluations", ["tenant_id", "application_id"])

    tenant = "(SELECT app_tenant_id())"
    for tbl in ("hr_candidates", "hr_candidate_applications", "hr_candidate_evaluations"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {tbl} "
            f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO opsmind_app")


def downgrade() -> None:
    for tbl in ("hr_candidate_evaluations", "hr_candidate_applications", "hr_candidates"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
    op.drop_index("ix_hr_eval_tenant_app", table_name="hr_candidate_evaluations")
    op.drop_index("ix_hr_candidate_evaluations_tenant_id", table_name="hr_candidate_evaluations")
    op.drop_table("hr_candidate_evaluations")
    op.drop_index("ix_hr_app_tenant_cand", table_name="hr_candidate_applications")
    op.drop_index("ix_hr_app_tenant_dept_job", table_name="hr_candidate_applications")
    op.drop_index("ix_hr_candidate_applications_tenant_id", table_name="hr_candidate_applications")
    op.drop_table("hr_candidate_applications")
    op.drop_index("ix_hr_candidates_tenant_dept", table_name="hr_candidates")
    op.drop_index("ix_hr_candidates_tenant_id", table_name="hr_candidates")
    op.drop_table("hr_candidates")
