"""add nullable department_members.role and workflow_versions.pause_reason

Revision ID: c4d9e2f1a8b3
Revises: b93c5d81e4f2
Create Date: 2026-11-17 09:50:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d9e2f1a8b3"
down_revision: str | None = "b93c5d81e4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "department_members",
        sa.Column(
            "role",
            sa.Enum(
                "admin",
                "builder",
                "reviewer",
                "viewer",
                name="department_role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "workflow_versions",
        sa.Column("pause_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_versions", "pause_reason")
    op.drop_column("department_members", "role")
