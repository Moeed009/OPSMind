"""Safe, auditable cleanup script for the 128 automated test-leak tenants (F-08).

Default mode is --dry-run (read-only, prints the explicit tenant list + per-table row counts,
rolls back with 0 deletes).
Pass --apply --confirm-count <N> ONLY after reviewing the printed list and taking a pg_dump.

Protected named tenants (including ms-teach and the 12 named zero-membership tenants)
and any tenant with memberships > 0 are explicitly excluded and cause an immediate abort.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import uuid

from sqlalchemy import create_engine, text

DEFAULT_IDS_FILE = pathlib.Path(__file__).resolve().parent / "test_leak_tenants_128.json"

PROTECTED_NAMED_TENANT_IDS: frozenset[str] = frozenset(
    {
        "d4d50856-d91c-453a-8eba-acfa4df07783",  # amk
        "de0adbb3-8195-45de-80eb-742e6007d6ff",  # amk-2cad
        "0fc5e150-01db-4980-bbca-6b3b9ec9f3d9",  # hp-company
        "1a4f8bd9-5dd4-42f6-a607-6d393f80112d",  # ms-teach
        "44e3b07d-ccd8-49c8-b8a6-e2aef187b324",  # kim
        "8e5276f0-5b1b-4b83-bdb3-5d025ed23c7c",  # abdul-moeed-s-workspace
        "f344a9e1-69ad-44ff-9870-ec19d7eb792f",  # company
        "37627caf-e535-48bd-9f80-0c92b328eca5",  # acem
        "f60b2211-0bbf-4c2f-9e0b-6e9e1ab0c1ca",  # opsmind-live-demo
        "5e905f96-f6a9-459a-8fc1-0d2c91303fba",  # acem-f38c
        "35d638ae-c729-46d4-a29b-96947fccfcaf",  # amk-0e2f
        "fcf67b56-02aa-4097-bd18-a8db6da1ebc7",  # lmk
        "cca0fab0-840a-446e-8888-10a7866a893b",  # acem-cc96 (active)
    }
)

# Child-to-parent FK-safe deletion order for all tenant-scoped tables
FK_SAFE_TABLES: tuple[tuple[str, str], ...] = (
    ("workflow_approvals", "tenant_id"),
    ("review_tasks", "tenant_id"),
    ("action_logs", "tenant_id"),
    ("guardrail_events", "tenant_id"),
    ("eval_results", "tenant_id"),
    ("eval_sets", "tenant_id"),
    ("ai_runs", "tenant_id"),
    ("usage_records", "tenant_id"),
    ("run_steps", "tenant_id"),
    ("runs", "tenant_id"),
    ("events", "tenant_id"),
    ("doc_chunks", "tenant_id"),
    ("documents", "tenant_id"),
    ("workflow_versions", "tenant_id"),
    ("workflows", "tenant_id"),
    ("department_members", "tenant_id"),
    ("connections", "tenant_id"),
    ("oauth_states", "tenant_id"),
    ("departments", "tenant_id"),
    ("invitations", "tenant_id"),
    ("email_settings", "tenant_id"),
    ("api_keys", "tenant_id"),
    ("subscriptions", "tenant_id"),
    ("audit_logs", "tenant_id"),
    ("memberships", "tenant_id"),
    ("tenants", "id"),
)


def load_target_tenants(ids_file: pathlib.Path = DEFAULT_IDS_FILE) -> list[tuple[str, str, str]]:
    raw = json.loads(ids_file.read_text(encoding="utf-8"))
    return [(str(r[0]), str(r[1]), str(r[2])) for r in raw]


def run_cleanup(
    database_url: str,
    target_tenants: list[tuple[str, str, str]],
    *,
    apply: bool = False,
    confirm_count: int | None = None,
) -> dict[str, int]:
    """Execute dry-run or transactional FK-safe deletion for an explicit list of tenant IDs."""
    target_ids = [str(uuid.UUID(tid)) for tid, _slug, _ts in target_tenants]
    if not target_ids:
        raise RuntimeError("ABORT: target tenant list is empty.")
    if len(target_ids) != len(set(target_ids)):
        raise RuntimeError("ABORT: duplicate tenant IDs in target list.")

    protected_overlap = set(target_ids) & PROTECTED_NAMED_TENANT_IDS
    if protected_overlap:
        raise RuntimeError(
            f"ABORT: target list contains protected tenant ID(s): {sorted(protected_overlap)}"
        )

    print(f"=== TARGET TENANTS ({len(target_tenants)}) ===")
    for tid, slug, ts in target_tenants:
        print(f"  {tid} | {slug} | {ts}")

    if apply and confirm_count != len(target_ids):
        raise RuntimeError(
            f"ABORT: --apply requires --confirm-count {len(target_ids)} (got {confirm_count})."
        )

    engine = create_engine(database_url, pool_pre_ping=True)
    counts: dict[str, int] = {}
    try:
        with engine.connect() as conn, conn.begin() as tx:
            found_rows = conn.execute(
                text("SELECT id::text, slug FROM tenants WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": target_ids},
            ).all()
            if len(found_rows) != len(target_ids):
                found_set = {r[0] for r in found_rows}
                missing = sorted(set(target_ids) - found_set)
                raise RuntimeError(
                    f"ABORT: expected {len(target_ids)} tenants in DB, found {len(found_rows)}. "
                    f"Missing ({len(missing)}): {missing[:5]}"
                )

            member_cnt = conn.execute(
                text(
                    "SELECT count(*) FROM memberships WHERE tenant_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": target_ids},
            ).scalar_one()
            if int(member_cnt) > 0:
                raise RuntimeError(
                    f"ABORT: {member_cnt} membership row(s) exist on target tenants; refusing to delete."
                )

            print("\n=== PER-TABLE ROW COUNTS (FK-SAFE ORDER) ===")
            for table, col in FK_SAFE_TABLES:
                cnt = int(
                    conn.execute(
                        text(
                            f"SELECT count(*) FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"
                        ),
                        {"ids": target_ids},
                    ).scalar_one()
                )
                counts[table] = cnt
                if cnt > 0 or table == "tenants":
                    print(f"  {table:22s}: {cnt}")

            if not apply:
                print("\nDRY-RUN COMPLETE: 0 rows deleted (transaction rolled back).")
                tx.rollback()
                return counts

            for table, col in FK_SAFE_TABLES:
                if counts[table] == 0 and table != "tenants":
                    continue
                res = conn.execute(
                    text(
                        f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": target_ids},
                )
                if table == "tenants" and res.rowcount != len(target_ids):
                    raise RuntimeError(
                        f"ABORT: expected to delete {len(target_ids)} tenants, deleted {res.rowcount}."
                    )

            print(f"\nAPPLY COMPLETE: deleted {counts['tenants']} test-leak tenants in 1 transaction.")
            return counts
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean up explicit test-leak tenants safely.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument(
        "--ids-file",
        default=str(DEFAULT_IDS_FILE),
        help="Path to JSON list of [id, slug, created_at]",
    )
    parser.add_argument("--apply", action="store_true", help="Execute deletions (default is dry-run)")
    parser.add_argument(
        "--confirm-count",
        type=int,
        default=None,
        help="Required with --apply; must match target tenant count",
    )
    args = parser.parse_args(argv)

    if not args.database_url:
        print("ERROR: --database-url or DATABASE_URL env var is required.", file=sys.stderr)
        return 2

    targets = load_target_tenants(pathlib.Path(args.ids_file))
    run_cleanup(
        args.database_url,
        targets,
        apply=args.apply,
        confirm_count=args.confirm_count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
