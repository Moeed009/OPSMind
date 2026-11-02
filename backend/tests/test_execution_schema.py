from sqlalchemy import Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import app.db.models  # noqa: F401
from app.db.base import Base

EXEMPT_PARENTS = {"tenants", "users"}
DIALECT = postgresql.dialect()


def ddl(table_name: str) -> str:
    table = Base.metadata.tables[table_name]
    parts = [str(CreateTable(table).compile(dialect=DIALECT))]
    parts += [str(CreateIndex(index).compile(dialect=DIALECT)) for index in table.indexes]
    return "\n".join(parts)


def has_unique_tenant_id(table: Table) -> bool:
    return any(
        isinstance(constraint, UniqueConstraint)
        and [column.name for column in constraint.columns] == ["tenant_id", "id"]
        for constraint in table.constraints
    )


def test_cross_table_foreign_keys_include_tenant_id():
    for table in Base.metadata.tables.values():
        for fk in table.foreign_key_constraints:
            if fk.referred_table.name in EXEMPT_PARENTS:
                continue
            assert "tenant_id" in fk.column_keys, f"{table.name}: {fk.name}"


def test_referenced_parents_are_unique_on_tenant_and_id():
    for table in Base.metadata.tables.values():
        for fk in table.foreign_key_constraints:
            if fk.referred_table.name in EXEMPT_PARENTS:
                continue
            assert has_unique_tenant_id(fk.referred_table), fk.referred_table.name


def test_dedup_and_idempotency_constraints_exist():
    assert "uq_events_tenant_id_connection_id_dedup_key" in ddl("events")
    assert "uq_action_logs_tenant_id_idempotency_key" in ddl("action_logs")
    assert "uq_runs_tenant_id_idempotency_key" in ddl("runs")


def test_single_published_version_and_single_open_review_task():
    assert "uq_workflow_versions_workflow_id_published" in ddl("workflow_versions")
    assert "uq_review_tasks_tenant_id_run_step_id_open" in ddl("review_tasks")