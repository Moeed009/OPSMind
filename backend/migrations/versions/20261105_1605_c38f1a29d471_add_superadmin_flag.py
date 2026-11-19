"""add is_superadmin column to users

Revision ID: c38f1a29d471
Revises: b52e8d1a3c40
Create Date: 2026-11-05 16:05:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c38f1a29d471'
down_revision: str | None = 'b52e8d1a3c40'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS is_superadmin BOOLEAN NOT NULL DEFAULT false;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_users_is_superadmin
        ON users(is_superadmin)
        WHERE is_superadmin = true;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_is_superadmin;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_superadmin;")
