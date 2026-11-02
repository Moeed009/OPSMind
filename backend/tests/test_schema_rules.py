from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import app.db.models  # noqa: F401
from app.db.base import Base

DIALECT = postgresql.dialect()


def ddl(table_name: str) -> str:
    table = Base.metadata.tables[table_name]
    parts = [str(CreateTable(table).compile(dialect=DIALECT))]
    parts += [str(CreateIndex(index).compile(dialect=DIALECT)) for index in table.indexes]
    return "\n".join(parts)


def test_all_tables_compile_for_postgres():
    for table in Base.metadata.sorted_tables:
        ddl(table.name)


def test_composite_unique_name_includes_all_columns():
    assert "uq_memberships_tenant_id_user_id" in ddl("memberships")


def test_tenant_slug_is_format_checked():
    assert "ck_tenants_slug_format" in ddl("tenants")


def test_only_one_live_subscription_per_tenant():
    statement = ddl("subscriptions")
    assert "uq_subscriptions_tenant_id_live" in statement
    assert "WHERE status IN" in statement