"""add superadmin rls bypass policies

Revision ID: d49e2b38f582
Revises: c38f1a29d471
Create Date: 2026-11-05 16:15:00.000000
"""
from collections.abc import Sequence
from alembic import op

revision: str = 'd49e2b38f582'
down_revision: str | None = 'c38f1a29d471'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ["users", "tenants", "memberships", "workflows", "connections"]


def upgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP POLICY IF EXISTS superadmin_access ON {t}")
        op.execute(
            f"""
            CREATE POLICY superadmin_access ON {t}
            USING (nullif(current_setting('app.is_superadmin', true), '') = 'true')
            WITH CHECK (nullif(current_setting('app.is_superadmin', true), '') = 'true')
            """
        )


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP POLICY IF EXISTS superadmin_access ON {t}")
