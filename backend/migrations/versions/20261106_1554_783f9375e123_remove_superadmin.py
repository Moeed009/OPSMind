"""remove_superadmin

Revision ID: 783f9375e123
Revises: e51b2c48f693
Create Date: 2026-11-06 15:54:27.192112
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '783f9375e123'
down_revision: str | None = 'e51b2c48f693'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ["users", "tenants", "memberships", "workflows", "connections"]


def upgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP POLICY IF EXISTS superadmin_access ON {t}")
    op.execute("DROP INDEX IF EXISTS ix_users_is_superadmin;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_superadmin;")


def downgrade() -> None:
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
    for t in TABLES:
        op.execute(
            f"""
            CREATE POLICY superadmin_access ON {t}
            USING (nullif(current_setting('app.is_superadmin', true), '') = 'true')
            WITH CHECK (nullif(current_setting('app.is_superadmin', true), '') = 'true')
            """
        )
