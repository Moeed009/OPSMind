import app.db.models  # noqa: F401
from app.db.base import Base

GLOBAL_TABLES = {"tenants", "users"}


def test_every_tenant_scoped_table_has_tenant_id():
    for name, table in Base.metadata.tables.items():
        if name not in GLOBAL_TABLES:
            assert "tenant_id" in table.c, f"{name} is missing tenant_id"