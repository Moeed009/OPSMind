"""extend hr_candidate_evaluations for structured AI matching results and idempotency

Revision ID: f3a8b1c0d7e2
Revises: e2f7a0b9c6d1
Create Date: 2026-11-18 15:35:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a8b1c0d7e2"
down_revision: str | None = "e2f7a0b9c6d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("hr_candidate_evaluations", sa.Column("candidate_id", sa.Uuid(), nullable=True))
    op.add_column("hr_candidate_evaluations", sa.Column("job_id", sa.Uuid(), nullable=True))
    op.add_column("hr_candidate_evaluations", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.add_column("hr_candidate_evaluations", sa.Column("ai_run_id", sa.Uuid(), nullable=True))
    op.add_column(
        "hr_candidate_evaluations",
        sa.Column("evaluation_source", sa.String(length=16), server_default="human", nullable=False),
    )
    op.add_column("hr_candidate_evaluations", sa.Column("overall_score", sa.Numeric(5, 2), nullable=True))
    op.add_column("hr_candidate_evaluations", sa.Column("confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column("hr_candidate_evaluations", sa.Column("recommendation", sa.String(length=32), nullable=True))
    op.add_column(
        "hr_candidate_evaluations",
        sa.Column(
            "matched_skills",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "hr_candidate_evaluations",
        sa.Column(
            "missing_skills",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "hr_candidate_evaluations",
        sa.Column("experience_match", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("hr_candidate_evaluations", sa.Column("ai_reasoning", sa.Text(), nullable=True))

    op.create_foreign_key(
        "fk_hr_eval_tenant_cand",
        "hr_candidate_evaluations",
        "hr_candidates",
        ["tenant_id", "candidate_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_hr_eval_tenant_job",
        "hr_candidate_evaluations",
        "hr_jobs",
        ["tenant_id", "job_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_hr_eval_tenant_doc",
        "hr_candidate_evaluations",
        "hr_candidate_documents",
        ["tenant_id", "document_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_hr_eval_airun",
        "hr_candidate_evaluations",
        "ai_runs",
        ["ai_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_hr_eval_ai_app_doc",
        "hr_candidate_evaluations",
        ["tenant_id", "application_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("evaluation_source = 'ai' AND document_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_hr_eval_ai_app_doc", table_name="hr_candidate_evaluations")
    op.drop_constraint("fk_hr_eval_airun", "hr_candidate_evaluations", type_="foreignkey")
    op.drop_constraint("fk_hr_eval_tenant_doc", "hr_candidate_evaluations", type_="foreignkey")
    op.drop_constraint("fk_hr_eval_tenant_job", "hr_candidate_evaluations", type_="foreignkey")
    op.drop_constraint("fk_hr_eval_tenant_cand", "hr_candidate_evaluations", type_="foreignkey")
    for col in (
        "ai_reasoning",
        "experience_match",
        "missing_skills",
        "matched_skills",
        "recommendation",
        "confidence",
        "overall_score",
        "evaluation_source",
        "ai_run_id",
        "document_id",
        "job_id",
        "candidate_id",
    ):
        op.drop_column("hr_candidate_evaluations", col)
