"""add oauth_states table

Revision ID: b52e8d1a3c40
Revises: a41c7d9e5b20
Create Date: 2026-11-05 10:55:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b52e8d1a3c40'
down_revision: str | None = 'a41c7d9e5b20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsmind_app') THEN
            RAISE EXCEPTION 'role opsmind_app must exist before this migration';
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_states (
            nonce TEXT PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            code_verifier TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_oauth_states_tenant_id ON oauth_states(tenant_id)")
    op.execute("ALTER TABLE oauth_states ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE oauth_states FORCE ROW LEVEL SECURITY")

    # Drop existing policy if present, then recreate
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON oauth_states")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON oauth_states
        USING (tenant_id = (SELECT app_tenant_id()))
        WITH CHECK (tenant_id = (SELECT app_tenant_id()))
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON oauth_states TO opsmind_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS oauth_states CASCADE")
