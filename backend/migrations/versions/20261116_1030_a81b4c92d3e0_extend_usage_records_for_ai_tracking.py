"""extend usage_records with AI task telemetry columns

Revision ID: a81b4c92d3e0
Revises: f62c3a95d7b1
Create Date: 2026-11-16 10:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a81b4c92d3e0"
down_revision: str | None = "f62c3a95d7b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage_records", sa.Column("task_type", sa.String(length=50), nullable=True))
    op.add_column(
        "usage_records",
        sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "usage_records",
        sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "usage_records",
        sa.Column("total_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("usage_records", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column(
        "usage_records",
        sa.Column("status", sa.String(length=30), server_default=sa.text("'succeeded'"), nullable=False),
    )
    op.add_column(
        "usage_records",
        sa.Column("is_estimated_cost", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("usage_records", sa.Column("correlation_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_records", "correlation_id")
    op.drop_column("usage_records", "is_estimated_cost")
    op.drop_column("usage_records", "status")
    op.drop_column("usage_records", "latency_ms")
    op.drop_column("usage_records", "total_tokens")
    op.drop_column("usage_records", "completion_tokens")
    op.drop_column("usage_records", "prompt_tokens")
    op.drop_column("usage_records", "task_type")
