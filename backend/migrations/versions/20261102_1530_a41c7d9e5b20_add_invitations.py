"""add invitations

Revision ID: a41c7d9e5b20
Revises: 776e7959a2ce
Create Date: 2026-11-02 15:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a41c7d9e5b20'
down_revision: str | None = '776e7959a2ce'
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

    op.create_table('invitations',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('role', sa.Enum('owner', 'admin', 'builder', 'reviewer', 'viewer', name='role', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('invited_by', sa.Uuid(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('accepted_by', sa.Uuid(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_invitations_email_lowercase')),
    sa.CheckConstraint("role <> 'owner'", name=op.f('ck_invitations_not_owner')),
    sa.ForeignKeyConstraint(['accepted_by'], ['users.id'], name=op.f('fk_invitations_accepted_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['invited_by'], ['users.id'], name=op.f('fk_invitations_invited_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_invitations_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invitations')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_invitations_token_hash'))
    )
    op.create_index(op.f('ix_invitations_tenant_id'), 'invitations', ['tenant_id'], unique=False)
    op.create_index('uq_invitations_pending_email', 'invitations', ['tenant_id', 'email'], unique=True, postgresql_where=sa.text('accepted_at IS NULL AND revoked_at IS NULL'))

    # RLS: baqi tenant tables jaisi policy
    op.execute("ALTER TABLE invitations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invitations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON invitations "
        "USING (tenant_id = (SELECT app_tenant_id())) "
        "WITH CHECK (tenant_id = (SELECT app_tenant_id()))"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON invitations TO opsmind_app")

    # Invitee abhi member nahi hota, isliye token se lookup ke liye chhota SECURITY DEFINER
    # function. Sirf token ke hash se ek invitation dikhata hai (token hi credential hai).
    op.execute(
        """
        CREATE FUNCTION app_find_invitation(p_token_hash text)
        RETURNS TABLE (
          id uuid, tenant_id uuid, email text, role text,
          expires_at timestamptz, accepted_at timestamptz, revoked_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT i.id, i.tenant_id, i.email::text, i.role::text,
                 i.expires_at, i.accepted_at, i.revoked_at
          FROM public.invitations i
          WHERE i.token_hash = p_token_hash
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION app_find_invitation(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app_find_invitation(text) TO opsmind_app")

    # Supabase Data API roles band (local Docker mein ye roles nahi hote)
    op.execute(
        """
        DO $$
        DECLARE r text;
        BEGIN
          FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
              EXECUTE 'REVOKE ALL ON TABLE invitations FROM ' || quote_ident(r);
              EXECUTE 'REVOKE ALL ON FUNCTION app_find_invitation(text) FROM '
                      || quote_ident(r);
            END IF;
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_find_invitation(text)")
    op.drop_index('uq_invitations_pending_email', table_name='invitations', postgresql_where=sa.text('accepted_at IS NULL AND revoked_at IS NULL'))
    op.drop_index(op.f('ix_invitations_tenant_id'), table_name='invitations')
    op.drop_table('invitations')