"""add hr_candidate_documents table with tenant isolation and RLS

Revision ID: d1e6f9a8b5c0
Revises: c0d5e8f7a4b9
Create Date: 2026-11-18 15:10:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e6f9a8b5c0"
down_revision: str | None = "c0d5e8f7a4b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hr_candidate_documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=127), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "upload",
                "gmail",
                name="candidate_document_source",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="upload",
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name="hr_cand_doc_size_positive"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name="fk_hr_cand_doc_tenant_dept",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["hr_candidates.tenant_id", "hr_candidates.id"],
            name="fk_hr_cand_doc_tenant_cand",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["hr_candidate_applications.tenant_id", "hr_candidate_applications.id"],
            name="fk_hr_cand_doc_tenant_app",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_hr_candidate_documents_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "candidate_id",
            "content_hash",
            name="uq_hr_cand_doc_tenant_cand_hash",
        ),
    )
    op.create_index("ix_hr_candidate_documents_tenant_id", "hr_candidate_documents", ["tenant_id"])
    op.create_index("ix_hr_cand_doc_tenant_cand", "hr_candidate_documents", ["tenant_id", "candidate_id"])

    tenant = "(SELECT app_tenant_id())"
    op.execute("ALTER TABLE hr_candidate_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hr_candidate_documents FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON hr_candidate_documents "
        f"USING (tenant_id = {tenant}) WITH CHECK (tenant_id = {tenant})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON hr_candidate_documents TO opsmind_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON hr_candidate_documents")
    op.drop_index("ix_hr_cand_doc_tenant_cand", table_name="hr_candidate_documents")
    op.drop_index("ix_hr_candidate_documents_tenant_id", table_name="hr_candidate_documents")
    op.drop_table("hr_candidate_documents")
