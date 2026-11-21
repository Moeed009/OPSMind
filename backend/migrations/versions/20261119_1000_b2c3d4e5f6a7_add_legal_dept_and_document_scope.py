"""add legal department_type and department_id/scope to documents

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-19 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE departments DROP CONSTRAINT IF EXISTS ck_departments_department_type;
        ALTER TABLE departments ADD CONSTRAINT ck_departments_department_type
            CHECK (department_type IN ('support', 'sales', 'hr', 'finance', 'marketing', 'legal', 'operations'));
        """
    )

    op.add_column("documents", sa.Column("department_id", sa.Uuid(), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "scope",
            sa.String(length=32),
            server_default="company_shared",
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_documents_tenant_dept",
        "documents",
        "departments",
        ["tenant_id", "department_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_tenant_scope_dept",
        "documents",
        ["tenant_id", "scope", "department_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_tenant_scope_dept", table_name="documents")
    op.drop_constraint("fk_documents_tenant_dept", "documents", type_="foreignkey")
    op.drop_column("documents", "scope")
    op.drop_column("documents", "department_id")
