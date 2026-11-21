"""drop cross-tenant email function; allow non-connector action logs

Revision ID: f62c3a95d7b1
Revises: 894e3d1b5a61
Create Date: 2026-11-12 12:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = "f62c3a95d7b1"
down_revision: str | None = "894e3d1b5a61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    import sqlalchemy as sa

    from app.services.email import encrypt_smtp_password

    op.execute("DROP FUNCTION IF EXISTS app_get_active_email_setting()")
    op.alter_column("action_logs", "connection_id", nullable=True)

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, tenant_id, smtp_password FROM email_settings "
            "WHERE smtp_password IS NOT NULL AND smtp_password != '' "
            "AND smtp_password NOT LIKE 'enc:v1:%'"
        )
    ).fetchall()
    for row in rows:
        encrypted = encrypt_smtp_password(row.smtp_password, row.tenant_id)
        bind.execute(
            sa.text("UPDATE email_settings SET smtp_password = :p WHERE id = :id"),
            {"p": encrypted, "id": row.id},
        )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_get_active_email_setting()
        RETURNS TABLE (
          id uuid, tenant_id uuid, smtp_host varchar, smtp_port integer,
          smtp_username varchar, smtp_password text, from_email varchar,
          from_name varchar, use_tls boolean, use_ssl boolean, is_active boolean
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT s.id, s.tenant_id, s.smtp_host, s.smtp_port,
                 s.smtp_username, s.smtp_password, s.from_email,
                 s.from_name, s.use_tls, s.use_ssl, s.is_active
          FROM public.email_settings s
          WHERE s.is_active = true
          ORDER BY s.updated_at DESC
          LIMIT 1;
        $$;
        REVOKE ALL ON FUNCTION app_get_active_email_setting() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION app_get_active_email_setting() TO opsmind_app;
        """
    )
    op.alter_column("action_logs", "connection_id", nullable=False)
