"""Tests for backend/scripts/cleanup_test_leak_tenants.py against the local test DB only."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from scripts.cleanup_test_leak_tenants import (
    DEFAULT_IDS_FILE,
    PROTECTED_NAMED_TENANT_IDS,
    load_target_tenants,
    run_cleanup,
)


def test_default_128_list_excludes_all_protected_named_tenants() -> None:
    targets = load_target_tenants(DEFAULT_IDS_FILE)
    assert len(targets) == 128
    target_ids = {tid for tid, _slug, _ts in targets}
    assert target_ids.isdisjoint(PROTECTED_NAMED_TENANT_IDS)
    assert "1a4f8bd9-5dd4-42f6-a607-6d393f80112d" not in target_ids  # ms-teach


def test_cleanup_script_dry_run_guards_and_transactional_apply_on_local_test_db() -> None:
    db_url = os.environ["DATABASE_URL"]
    engine = create_engine(db_url, pool_pre_ping=True)

    leak_1 = str(uuid.uuid4())
    leak_2 = str(uuid.uuid4())
    keeper = str(uuid.uuid4())
    dept_1 = str(uuid.uuid4())
    wf_1 = str(uuid.uuid4())

    with engine.connect() as conn, conn.begin():
        for tid, slug in (
            (leak_1, f"mi-clean-{leak_1[:8]}"),
            (leak_2, f"exec-clean-{leak_2[:8]}"),
            (keeper, f"keeper-{keeper[:8]}"),
        ):
            conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, region, plan) "
                    "VALUES (:id, :name, :slug, 'us', 'free')"
                ),
                {"id": tid, "name": slug, "slug": slug},
            )
        conn.execute(
            text(
                "INSERT INTO departments (id, tenant_id, name, code, department_type, status) "
                "VALUES (:did, :tid, 'Support', 'support', 'support', 'active')"
            ),
            {"did": dept_1, "tid": leak_1},
        )
        conn.execute(
            text(
                "INSERT INTO workflows (id, tenant_id, department_id, name) "
                "VALUES (:wid, :tid, :did, 'Leak WF')"
            ),
            {"wid": wf_1, "tid": leak_1, "did": dept_1},
        )
        conn.execute(
            text(
                "INSERT INTO audit_logs (id, tenant_id, action, entity_type) "
                "VALUES (:aid, :tid, 'tenant.created', 'tenant')"
            ),
            {"aid": str(uuid.uuid4()), "tid": leak_2},
        )

    targets = [
        (leak_1, f"mi-clean-{leak_1[:8]}", "2026-11-20 00:00:00"),
        (leak_2, f"exec-clean-{leak_2[:8]}", "2026-11-20 00:00:00"),
    ]

    try:
        # 1. Protected tenant (ms-teach) in target list -> aborts immediately
        with pytest.raises(RuntimeError, match="protected tenant"):
            run_cleanup(
                db_url,
                [*targets, ("1a4f8bd9-5dd4-42f6-a607-6d393f80112d", "ms-teach", "2026-11-06 10:39:53")],
                apply=False,
            )

        # 2. Count mismatch (extra missing UUID) -> aborts
        with pytest.raises(RuntimeError, match="expected 3 tenants in DB, found 2"):
            run_cleanup(
                db_url,
                [*targets, (str(uuid.uuid4()), "missing-1", "2026-11-20 00:00:00")],
                apply=False,
            )

        # 3. Dry-run -> reports exact counts and deletes 0 rows
        dry_counts = run_cleanup(db_url, targets, apply=False)
        assert dry_counts["tenants"] == 2
        assert dry_counts["departments"] == 1
        assert dry_counts["workflows"] == 1
        assert dry_counts["audit_logs"] == 1

        with engine.connect() as conn:
            still_there = conn.execute(
                text("SELECT count(*) FROM tenants WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [leak_1, leak_2, keeper]},
            ).scalar_one()
            assert still_there == 3

        # 4. Apply without matching --confirm-count -> aborts
        with pytest.raises(RuntimeError, match="--confirm-count 2"):
            run_cleanup(db_url, targets, apply=True, confirm_count=99)

        # 5. Apply with matching --confirm-count -> deletes ONLY leak_1 & leak_2; keeper untouched
        applied = run_cleanup(db_url, targets, apply=True, confirm_count=2)
        assert applied["tenants"] == 2

        with engine.connect() as conn:
            rem_leaks = conn.execute(
                text("SELECT count(*) FROM tenants WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [leak_1, leak_2]},
            ).scalar_one()
            rem_keeper = conn.execute(
                text("SELECT count(*) FROM tenants WHERE id = :id"),
                {"id": keeper},
            ).scalar_one()
            assert rem_leaks == 0
            assert rem_keeper == 1
    finally:
        with engine.connect() as conn, conn.begin():
            conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": keeper})
        engine.dispose()
