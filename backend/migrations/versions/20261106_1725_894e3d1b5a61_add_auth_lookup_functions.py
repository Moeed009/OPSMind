"""add auth and email lookup security functions

Revision ID: 894e3d1b5a61
Revises: 783f9375e123
Create Date: 2026-11-06 17:25:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = "894e3d1b5a61"
down_revision: str | None = "783f9375e123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_find_user_by_email(p_email text)
        RETURNS TABLE (
          id uuid, email varchar, full_name varchar, is_active boolean
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT u.id, u.email, u.full_name, u.is_active
          FROM public.users u
          WHERE lower(u.email) = lower(p_email)
          LIMIT 1;
        $$;
        REVOKE ALL ON FUNCTION app_find_user_by_email(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION app_find_user_by_email(text) TO opsmind_app;
        """
    )

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


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_find_user_by_email(text)")
    op.execute("DROP FUNCTION IF EXISTS app_get_active_email_setting()")
