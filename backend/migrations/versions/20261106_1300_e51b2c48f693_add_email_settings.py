"""add email_settings

Revision ID: e51b2c48f693
Revises: d49e2b38f582
Create Date: 2026-11-06 13:00:00.000000
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = 'e51b2c48f693'
down_revision: str | None = 'd49e2b38f582'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'email_settings',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('provider', sa.String(length=50), server_default='smtp', nullable=False),
        sa.Column('smtp_host', sa.String(length=255), nullable=True),
        sa.Column('smtp_port', sa.Integer(), server_default='587', nullable=False),
        sa.Column('smtp_username', sa.String(length=255), nullable=True),
        sa.Column('smtp_password', sa.Text(), nullable=True),
        sa.Column('from_email', sa.String(length=255), server_default='noreply@opsmind.internal', nullable=False),
        sa.Column('from_name', sa.String(length=100), server_default='OpsMind', nullable=False),
        sa.Column('use_tls', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('use_ssl', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_email_settings_tenant_id_tenants'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_email_settings')),
        sa.UniqueConstraint('tenant_id', name=op.f('uq_email_settings_tenant_id'))
    )
    op.create_index(op.f('ix_email_settings_tenant_id'), 'email_settings', ['tenant_id'], unique=True)

    
    op.execute("ALTER TABLE email_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE email_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON email_settings "
        "USING (tenant_id = (SELECT app_tenant_id())) "
        "WITH CHECK (tenant_id = (SELECT app_tenant_id()))"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON email_settings TO opsmind_app")


def downgrade() -> None:
    op.drop_table('email_settings')
