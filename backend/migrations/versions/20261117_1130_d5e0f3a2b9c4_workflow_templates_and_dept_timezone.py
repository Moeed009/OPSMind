"""add departments.timezone and workflow_templates table

Revision ID: d5e0f3a2b9c4
Revises: c4d9e2f1a8b3
Create Date: 2026-11-17 11:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5e0f3a2b9c4"
down_revision: str | None = "c4d9e2f1a8b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "departments",
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="Asia/Karachi",
            nullable=False,
        ),
    )
    op.create_table(
        "workflow_templates",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("department_type", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "required_connectors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index(
        "ix_workflow_templates_department_type",
        "workflow_templates",
        ["department_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_templates_department_type", table_name="workflow_templates")
    op.drop_table("workflow_templates")
    op.drop_column("departments", "timezone")
