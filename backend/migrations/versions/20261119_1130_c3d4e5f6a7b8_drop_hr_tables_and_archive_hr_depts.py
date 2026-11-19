"""Phase 2 HR removal: archive HR departments, drop HR tables, and restrict department_type.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-19 11:30:00.000000

NOTE: DO NOT APPLY until operator confirms backup/export is complete.
`downgrade()` recreates empty HR tables and restores the previous check constraint,
but CANNOT restore dropped rows or unarchive archived departments.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HR_TABLES = (
    "hr_candidate_exports",
    "hr_candidate_evaluations",
    "hr_candidate_documents",
    "hr_candidate_applications",
    "hr_ingestion_exceptions",
    "hr_candidates",
    "hr_job_requirements",
    "hr_jobs",
)


def upgrade() -> None:
    # 1. Archive HR/Marketing departments & pause any attached workflows (non-destructive to audit/run history).
    # Uses lowercase enum values ('paused', 'archived') and distinct archived name/code suffix
    # so it never collides with an existing active Operations department in the same tenant
    # and is excluded from active department listings (status != 'archived').
    op.execute(
        """
        UPDATE workflow_versions wv
        SET status = 'paused',
            pause_reason = COALESCE(pause_reason, 'HR department archived during Phase 2 cleanup')
        FROM workflows w
        JOIN departments d ON d.id = w.department_id
        WHERE wv.workflow_id = w.id
          AND d.department_type IN ('hr', 'marketing')
          AND LOWER(wv.status) = 'published'
        """
    )
    op.execute(
        """
        UPDATE departments
        SET status = 'archived',
            archived_at = COALESCE(archived_at, NOW()),
            name = CASE
                WHEN name LIKE '% (archived%' THEN name
                ELSE LEFT(name, 95) || ' (archived-' || SUBSTRING(id::text, 1, 6) || ')'
            END,
            code = CASE
                WHEN code LIKE 'arch_%' THEN code
                ELSE LEFT('arch_' || code || '_' || SUBSTRING(id::text, 1, 4), 50)
            END,
            department_type = 'operations'
        WHERE department_type IN ('hr', 'marketing')
        """
    )

    # 2. Drop HR-only tables in FK-safe order
    for table in _HR_TABLES:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))

    # 3. Restrict departments.department_type CHECK constraint to the 5 approved types
    op.execute(
        """
        ALTER TABLE departments DROP CONSTRAINT IF EXISTS ck_departments_department_type;
        ALTER TABLE departments ADD CONSTRAINT ck_departments_department_type
            CHECK (department_type IN ('support', 'finance', 'sales', 'legal', 'operations'));
        """
    )


def downgrade() -> None:
    # Restores schema constraint only; dropped HR rows CANNOT be restored by downgrade.
    op.execute(
        """
        ALTER TABLE departments DROP CONSTRAINT IF EXISTS ck_departments_department_type;
        ALTER TABLE departments ADD CONSTRAINT ck_departments_department_type
            CHECK (department_type IN ('support', 'sales', 'hr', 'finance', 'marketing', 'legal', 'operations'));
        """
    )
