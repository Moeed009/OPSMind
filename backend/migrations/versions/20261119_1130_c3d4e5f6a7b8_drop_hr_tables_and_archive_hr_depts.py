"""Phase 2 HR removal: delete HR departments & dependents, drop 8 HR tables, restrict department_type.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-19 11:30:00.000000

NOTE: DO NOT APPLY until operator confirms JSON backup/export is complete.
Before deleting any HR department, this migration aborts with an error if any
HR department has referencing rows in `runs`, `review_tasks`, `workflow_approvals`,
or `action_logs`.

IRREVERSIBLE DATA DELETION:
`downgrade()` ONLY restores the previous `ck_departments_department_type` CHECK
constraint. It CANNOT restore deleted HR departments, their deleted dependent
rows (workflows, workflow_versions, department_members, connections/credentials,
documents, doc_chunks, events, ai_runs, usage_records, eval_sets/results), or
the 8 dropped HR tables.
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
    # 1. Safety abort guard: refuse to delete HR departments if any execution history
    #    (runs, review_tasks, workflow_approvals, or action_logs) references them.
    op.execute(
        """
        DO $$
        DECLARE
            v_runs_cnt BIGINT := 0;
            v_reviews_cnt BIGINT := 0;
            v_approvals_cnt BIGINT := 0;
            v_actions_cnt BIGINT := 0;
        BEGIN
            SELECT COUNT(*) INTO v_runs_cnt
            FROM runs r
            WHERE r.department_id IN (SELECT id FROM departments WHERE department_type = 'hr')
               OR r.workflow_id IN (
                   SELECT w.id FROM workflows w
                   JOIN departments d ON d.id = w.department_id
                   WHERE d.department_type = 'hr'
               )
               OR r.workflow_version_id IN (
                   SELECT wv.id FROM workflow_versions wv
                   JOIN workflows w ON w.id = wv.workflow_id
                   JOIN departments d ON d.id = w.department_id
                   WHERE d.department_type = 'hr'
               );

            SELECT COUNT(*) INTO v_reviews_cnt
            FROM review_tasks rt
            WHERE rt.run_id IN (
                SELECT r.id FROM runs r
                WHERE r.department_id IN (SELECT id FROM departments WHERE department_type = 'hr')
                   OR r.workflow_id IN (
                       SELECT w.id FROM workflows w
                       JOIN departments d ON d.id = w.department_id
                       WHERE d.department_type = 'hr'
                   )
            );

            SELECT COUNT(*) INTO v_approvals_cnt
            FROM workflow_approvals wa
            WHERE wa.department_id IN (SELECT id FROM departments WHERE department_type = 'hr')
               OR wa.run_id IN (
                   SELECT r.id FROM runs r
                   WHERE r.department_id IN (SELECT id FROM departments WHERE department_type = 'hr')
               );

            SELECT COUNT(*) INTO v_actions_cnt
            FROM action_logs al
            WHERE al.connection_id IN (
                SELECT c.id FROM connections c
                JOIN departments d ON d.id = c.department_id
                WHERE d.department_type = 'hr'
            )
               OR al.run_id IN (
                   SELECT r.id FROM runs r
                   WHERE r.department_id IN (SELECT id FROM departments WHERE department_type = 'hr')
               );

            IF v_runs_cnt > 0 OR v_reviews_cnt > 0 OR v_approvals_cnt > 0 OR v_actions_cnt > 0 THEN
                RAISE EXCEPTION
                    'Aborting HR department deletion (c3d4e5f6a7b8): HR departments still have execution history (runs=%, review_tasks=%, workflow_approvals=%, action_logs=%). Resolve or export/clear these before running migration.',
                    v_runs_cnt, v_reviews_cnt, v_approvals_cnt, v_actions_cnt;
            END IF;
        END $$;
        """
    )

    # 2. Drop the 8 HR-only tables in FK-safe order first
    for table in _HR_TABLES:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))

    # 3. Delete all dependents of departments with department_type = 'hr' in FK-safe order
    op.execute(
        """
        -- 3a. Evaluation results & sets for HR workflows
        DELETE FROM eval_results
        WHERE workflow_version_id IN (
            SELECT wv.id FROM workflow_versions wv
            JOIN workflows w ON w.id = wv.workflow_id
            JOIN departments d ON d.id = w.department_id
            WHERE d.department_type = 'hr'
        )
        OR eval_set_id IN (
            SELECT es.id FROM eval_sets es
            JOIN workflows w ON w.id = es.workflow_id
            JOIN departments d ON d.id = w.department_id
            WHERE d.department_type = 'hr'
        );

        DELETE FROM eval_sets
        WHERE workflow_id IN (
            SELECT w.id FROM workflows w
            JOIN departments d ON d.id = w.department_id
            WHERE d.department_type = 'hr'
        );

        -- 3b. AI runs, usage records, and events scoped to HR departments or HR connections
        DELETE FROM ai_runs
        WHERE department_id IN (SELECT id FROM departments WHERE department_type = 'hr');

        DELETE FROM usage_records
        WHERE department_id IN (SELECT id FROM departments WHERE department_type = 'hr')
           OR workflow_id IN (
               SELECT w.id FROM workflows w
               JOIN departments d ON d.id = w.department_id
               WHERE d.department_type = 'hr'
           );

        DELETE FROM events
        WHERE department_id IN (SELECT id FROM departments WHERE department_type = 'hr')
           OR connection_id IN (
               SELECT c.id FROM connections c
               JOIN departments d ON d.id = c.department_id
               WHERE d.department_type = 'hr'
           );

        -- 3c. Workflow versions & workflows owned by HR departments
        DELETE FROM workflow_versions
        WHERE workflow_id IN (
            SELECT w.id FROM workflows w
            JOIN departments d ON d.id = w.department_id
            WHERE d.department_type = 'hr'
        )
        OR trigger_connection_id IN (
            SELECT c.id FROM connections c
            JOIN departments d ON d.id = c.department_id
            WHERE d.department_type = 'hr'
        );

        DELETE FROM workflows
        WHERE department_id IN (SELECT id FROM departments WHERE department_type = 'hr');

        -- 3d. Knowledge Hub doc_chunks & documents scoped to HR departments or HR connections
        DELETE FROM doc_chunks
        WHERE document_id IN (
            SELECT doc.id FROM documents doc
            WHERE doc.department_id IN (SELECT id FROM departments WHERE department_type = 'hr')
               OR doc.connection_id IN (
                   SELECT c.id FROM connections c
                   JOIN departments d ON d.id = c.department_id
                   WHERE d.department_type = 'hr'
               )
        );

        DELETE FROM documents
        WHERE department_id IN (SELECT id FROM departments WHERE department_type = 'hr')
           OR connection_id IN (
               SELECT c.id FROM connections c
               JOIN departments d ON d.id = c.department_id
               WHERE d.department_type = 'hr'
           );

        -- 3e. Wipe credentials on HR department-owned connections before deleting them
        UPDATE connections
        SET credentials_ciphertext = NULL,
            credentials_key_id = NULL
        WHERE department_id IN (SELECT id FROM departments WHERE department_type = 'hr');

        DELETE FROM connections
        WHERE department_id IN (SELECT id FROM departments WHERE department_type = 'hr');

        -- 3f. Department memberships
        DELETE FROM department_members
        WHERE department_id IN (SELECT id FROM departments WHERE department_type = 'hr');

        -- 3g. Delete the HR departments themselves
        DELETE FROM departments
        WHERE department_type = 'hr';
        """
    )

    # 4. Restrict departments.department_type CHECK constraint to the 5 approved types
    op.execute(
        """
        ALTER TABLE departments DROP CONSTRAINT IF EXISTS ck_departments_department_type;
        ALTER TABLE departments ADD CONSTRAINT ck_departments_department_type
            CHECK (department_type IN ('support', 'finance', 'sales', 'legal', 'operations'));
        """
    )


def downgrade() -> None:
    # Restores the previous department_type CHECK constraint only.
    # Deleted HR departments, their dependents, wiped credentials, and the 8 dropped HR tables
    # CANNOT be restored by downgrade.
    op.execute(
        """
        ALTER TABLE departments DROP CONSTRAINT IF EXISTS ck_departments_department_type;
        ALTER TABLE departments ADD CONSTRAINT ck_departments_department_type
            CHECK (department_type IN ('support', 'sales', 'hr', 'finance', 'marketing', 'legal', 'operations'));
        """
    )
