"""Clean up leftover HR/Marketing departments and re-type mislabeled departments.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-19 18:00:00.000000

NOTE: DO NOT APPLY until operator confirms.
1. Matches strictly by `department_type = 'operations'` AND exact `template_key` / `code`
   (NEVER by fuzzy `LOWER(name) LIKE '%...%'`), so unrelated departments such as
   "Sales Ops" (`code='sales-ops'`) or "Marketing Analytics" (`code='marketing-analytics'`)
   are never touched.
2. Logs pre-flight affected row counts (`RAISE NOTICE`) before making any writes and
   ABORTS (`RAISE EXCEPTION`) if the number of rows to delete exceeds 1 or the number
   of rows to re-type exceeds 3 (matching the read-only audit cap).
3. Leftover HR/Marketing rows (`code` or `template_key` in `('hr', 'marketing')`,
   `department_type = 'operations'`) with zero execution history are deleted in
   FK-safe order (wiping connection credentials first); any with execution history
   are archived with published workflows paused.
4. Mislabeled active departments (`department_type = 'operations'` with exact
   `template_key` or `code` in `('support', 'finance', 'sales', 'legal')`) are
   re-typed to their canonical approved `department_type` without altering `name`
   or `code`.

IRREVERSIBLE DATA CHANGE:
`downgrade()` is a no-op. It CANNOT restore deleted HR/Marketing departments or
their dependents, and it CANNOT restore previous `department_type` values for
re-typed departments.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            rec RECORD;
            v_hist_cnt BIGINT;
            v_delete_cnt BIGINT := 0;
            v_archive_cnt BIGINT := 0;
            v_retype_support BIGINT := 0;
            v_retype_finance BIGINT := 0;
            v_retype_sales BIGINT := 0;
            v_retype_legal BIGINT := 0;
            v_retype_total BIGINT := 0;
        BEGIN
            -- 1. Pre-flight count check & logging
            FOR rec IN
                SELECT id
                FROM departments
                WHERE department_type = 'operations'
                  AND status <> 'archived'
                  AND (
                      LOWER(COALESCE(template_key, '')) IN ('hr', 'marketing')
                      OR LOWER(code) IN ('hr', 'marketing')
                  )
            LOOP
                SELECT
                    (SELECT COUNT(*) FROM runs r
                     WHERE r.department_id = rec.id
                        OR r.workflow_id IN (SELECT w.id FROM workflows w WHERE w.department_id = rec.id))
                  + (SELECT COUNT(*) FROM review_tasks rt
                     WHERE rt.run_id IN (SELECT r.id FROM runs r WHERE r.department_id = rec.id))
                  + (SELECT COUNT(*) FROM workflow_approvals wa
                     WHERE wa.department_id = rec.id)
                  + (SELECT COUNT(*) FROM action_logs al
                     WHERE al.connection_id IN (SELECT c.id FROM connections c WHERE c.department_id = rec.id)
                        OR al.run_id IN (SELECT r.id FROM runs r WHERE r.department_id = rec.id))
                INTO v_hist_cnt;

                IF v_hist_cnt = 0 THEN
                    v_delete_cnt := v_delete_cnt + 1;
                ELSE
                    v_archive_cnt := v_archive_cnt + 1;
                END IF;
            END LOOP;

            SELECT COUNT(*) INTO v_retype_support
            FROM departments
            WHERE department_type = 'operations'
              AND status <> 'archived'
              AND (LOWER(COALESCE(template_key, '')) = 'support' OR LOWER(code) = 'support');

            SELECT COUNT(*) INTO v_retype_finance
            FROM departments
            WHERE department_type = 'operations'
              AND status <> 'archived'
              AND (LOWER(COALESCE(template_key, '')) = 'finance' OR LOWER(code) = 'finance');

            SELECT COUNT(*) INTO v_retype_sales
            FROM departments
            WHERE department_type = 'operations'
              AND status <> 'archived'
              AND (LOWER(COALESCE(template_key, '')) = 'sales' OR LOWER(code) = 'sales');

            SELECT COUNT(*) INTO v_retype_legal
            FROM departments
            WHERE department_type = 'operations'
              AND status <> 'archived'
              AND (LOWER(COALESCE(template_key, '')) = 'legal' OR LOWER(code) = 'legal');

            v_retype_total := v_retype_support + v_retype_finance + v_retype_sales + v_retype_legal;

            RAISE NOTICE 'e5f6a7b8c9d0 pre-flight counts: delete=%, archive=%, retype_total=% (support=%, finance=%, sales=%, legal=%)',
                v_delete_cnt, v_archive_cnt, v_retype_total,
                v_retype_support, v_retype_finance, v_retype_sales, v_retype_legal;

            IF v_delete_cnt > 1 OR v_retype_total > 3 THEN
                RAISE EXCEPTION
                    'Aborting e5f6a7b8c9d0: affected row counts exceed audit cap (delete=% [max 1], retype_total=% [max 3]). Re-audit before applying.',
                    v_delete_cnt, v_retype_total;
            END IF;

            -- 2. Delete HR/Marketing leftovers with 0 history; archive any with history > 0
            FOR rec IN
                SELECT id, name, code
                FROM departments
                WHERE department_type = 'operations'
                  AND status <> 'archived'
                  AND (
                      LOWER(COALESCE(template_key, '')) IN ('hr', 'marketing')
                      OR LOWER(code) IN ('hr', 'marketing')
                  )
            LOOP
                SELECT
                    (SELECT COUNT(*) FROM runs r
                     WHERE r.department_id = rec.id
                        OR r.workflow_id IN (SELECT w.id FROM workflows w WHERE w.department_id = rec.id))
                  + (SELECT COUNT(*) FROM review_tasks rt
                     WHERE rt.run_id IN (SELECT r.id FROM runs r WHERE r.department_id = rec.id))
                  + (SELECT COUNT(*) FROM workflow_approvals wa
                     WHERE wa.department_id = rec.id)
                  + (SELECT COUNT(*) FROM action_logs al
                     WHERE al.connection_id IN (SELECT c.id FROM connections c WHERE c.department_id = rec.id)
                        OR al.run_id IN (SELECT r.id FROM runs r WHERE r.department_id = rec.id))
                INTO v_hist_cnt;

                IF v_hist_cnt = 0 THEN
                    DELETE FROM eval_results
                    WHERE workflow_version_id IN (
                        SELECT wv.id FROM workflow_versions wv
                        JOIN workflows w ON w.id = wv.workflow_id
                        WHERE w.department_id = rec.id
                    )
                    OR eval_set_id IN (
                        SELECT es.id FROM eval_sets es
                        JOIN workflows w ON w.id = es.workflow_id
                        WHERE w.department_id = rec.id
                    );

                    DELETE FROM eval_sets
                    WHERE workflow_id IN (SELECT w.id FROM workflows w WHERE w.department_id = rec.id);

                    DELETE FROM ai_runs WHERE department_id = rec.id;

                    DELETE FROM usage_records
                    WHERE department_id = rec.id
                       OR workflow_id IN (SELECT w.id FROM workflows w WHERE w.department_id = rec.id);

                    DELETE FROM events
                    WHERE department_id = rec.id
                       OR connection_id IN (SELECT c.id FROM connections c WHERE c.department_id = rec.id);

                    DELETE FROM workflow_versions
                    WHERE workflow_id IN (SELECT w.id FROM workflows w WHERE w.department_id = rec.id)
                       OR trigger_connection_id IN (SELECT c.id FROM connections c WHERE c.department_id = rec.id);

                    DELETE FROM workflows WHERE department_id = rec.id;

                    DELETE FROM doc_chunks
                    WHERE document_id IN (
                        SELECT doc.id FROM documents doc
                        WHERE doc.department_id = rec.id
                           OR doc.connection_id IN (SELECT c.id FROM connections c WHERE c.department_id = rec.id)
                    );

                    DELETE FROM documents
                    WHERE department_id = rec.id
                       OR connection_id IN (SELECT c.id FROM connections c WHERE c.department_id = rec.id);

                    UPDATE connections
                    SET credentials_ciphertext = NULL,
                        credentials_key_id = NULL,
                        sync_cursor = NULL
                    WHERE department_id = rec.id;

                    DELETE FROM connections WHERE department_id = rec.id;
                    DELETE FROM department_members WHERE department_id = rec.id;
                    DELETE FROM departments WHERE id = rec.id;
                ELSE
                    UPDATE workflow_versions wv
                    SET status = 'paused',
                        pause_reason = COALESCE(pause_reason, 'Legacy HR/Marketing department archived during cleanup')
                    FROM workflows w
                    WHERE wv.workflow_id = w.id
                      AND w.department_id = rec.id
                      AND LOWER(wv.status) = 'published';

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
                        END
                    WHERE id = rec.id;
                END IF;
            END LOOP;

            -- 3. Re-type mislabeled active departments by exact template_key / code only
            UPDATE departments
            SET department_type = 'support'
            WHERE department_type = 'operations'
              AND status <> 'archived'
              AND (LOWER(COALESCE(template_key, '')) = 'support' OR LOWER(code) = 'support');

            UPDATE departments
            SET department_type = 'finance'
            WHERE department_type = 'operations'
              AND status <> 'archived'
              AND (LOWER(COALESCE(template_key, '')) = 'finance' OR LOWER(code) = 'finance');

            UPDATE departments
            SET department_type = 'sales'
            WHERE department_type = 'operations'
              AND status <> 'archived'
              AND (LOWER(COALESCE(template_key, '')) = 'sales' OR LOWER(code) = 'sales');

            UPDATE departments
            SET department_type = 'legal'
            WHERE department_type = 'operations'
              AND status <> 'archived'
              AND (LOWER(COALESCE(template_key, '')) = 'legal' OR LOWER(code) = 'legal');
        END $$;
        """
    )


def downgrade() -> None:
    # Irreversible data cleanup migration:
    # Downgrade CANNOT restore deleted HR/Marketing departments/dependents or
    # previous department_type values for re-typed departments.
    pass
