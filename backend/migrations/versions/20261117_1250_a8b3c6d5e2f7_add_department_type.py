"""add department_type enum column to departments

Revision ID: a8b3c6d5e2f7
Revises: f7a2b5c4d1e6
Create Date: 2026-11-17 12:50:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b3c6d5e2f7"
down_revision: str | None = "f7a2b5c4d1e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "departments",
        sa.Column(
            "department_type",
            sa.Enum(
                "support",
                "sales",
                "hr",
                "finance",
                "marketing",
                "operations",
                name="department_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="operations",
            nullable=False,
        ),
    )
    op.execute("GRANT SELECT ON workflow_templates TO opsmind_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON workflow_templates FROM opsmind_app")
    op.drop_column("departments", "department_type")
