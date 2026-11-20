"""Phase 2 connector cleanup: revoke/remove unapproved provider connections.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-11-19 12:10:00.000000

NOTE: DO NOT APPLY until operator confirms.
Removes any leftover connections for unapproved providers (github, jira, notion,
salesforce, zendesk, zoho_invoice) that have no action_log/event references,
and revokes any referenced ones while wiping their encrypted credentials.
`downgrade()` drops the `ck_connections_provider` constraint only; it CANNOT
restore deleted connection rows or wiped credentials.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REMOVED_PROVIDERS = "('github', 'jira', 'notion', 'salesforce', 'zendesk', 'zoho_invoice')"
_APPROVED_PROVIDERS = "('gmail', 'google_drive', 'google_calendar', 'google_sheets', 'slack', 'hubspot', 'stripe', 'zoho_books')"


def upgrade() -> None:
    # 1. Revoke any referenced connections and wipe encrypted credentials/cursor
    op.execute(
        f"""
        UPDATE connections
        SET status = 'revoked',
            credentials_ciphertext = NULL,
            credentials_key_id = NULL,
            sync_cursor = NULL
        WHERE provider IN {_REMOVED_PROVIDERS}
          AND (
              EXISTS (SELECT 1 FROM action_logs al WHERE al.connection_id = connections.id)
              OR EXISTS (SELECT 1 FROM events ev WHERE ev.connection_id = connections.id)
          )
        """
    )
    # 2. Delete unreferenced connections for removed providers
    op.execute(
        f"""
        DELETE FROM connections
        WHERE provider IN {_REMOVED_PROVIDERS}
          AND NOT EXISTS (SELECT 1 FROM action_logs al WHERE al.connection_id = connections.id)
          AND NOT EXISTS (SELECT 1 FROM events ev WHERE ev.connection_id = connections.id)
        """
    )
    # 3. Enforce provider check constraint so removed providers can never be active or hold credentials
    op.execute(
        f"""
        ALTER TABLE connections DROP CONSTRAINT IF EXISTS ck_connections_provider;
        ALTER TABLE connections ADD CONSTRAINT ck_connections_provider
            CHECK (
                provider IN {_APPROVED_PROVIDERS}
                OR (status = 'revoked' AND credentials_ciphertext IS NULL)
            );
        """
    )


def downgrade() -> None:
    # Restores schema constraint only; deleted connection rows and wiped credentials cannot be restored.
    op.execute("ALTER TABLE connections DROP CONSTRAINT IF EXISTS ck_connections_provider;")
