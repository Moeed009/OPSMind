"""Tenant isolation proof for Postgres RLS (AC-3, FR-1.5, NFR-6).

Seed/cleanup owner connection (DATABASE_URL) se hota hai. Saari checks
opsmind_app role (APP_DATABASE_URL) se hoti hain, jaise asli backend karega.
"""

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.config import get_settings

ALL_TABLES = [
    "action_logs", "api_keys", "audit_logs", "connections", "doc_chunks",
    "documents", "eval_results", "eval_sets", "events", "guardrail_events",
    "memberships", "review_tasks", "run_steps", "runs", "subscriptions",
    "tenants", "usage_records", "users", "workflow_versions", "workflows",
]


@pytest.fixture(scope="module")
def owner_engine():
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except OperationalError:
        pytest.skip("owner database not reachable")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def app_engine():
    engine = create_engine(
        get_settings().app_database_url,
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except OperationalError:
        pytest.skip("app database not reachable")
    yield engine
    engine.dispose()


@contextmanager
def tenant_conn(engine, tenant_id=None, user_id=None):
    """Ek transaction; settings transaction-scoped (set_config(..., true))."""
    with engine.connect() as conn, conn.begin():
        if tenant_id is not None:
            conn.execute(
                text("select set_config('app.current_tenant', :v, true)"),
                {"v": str(tenant_id)},
            )
        if user_id is not None:
            conn.execute(
                text("select set_config('app.current_user_id', :v, true)"),
                {"v": str(user_id)},
            )
        yield conn


@pytest.fixture(scope="module")
def seed(owner_engine):
    suffix = uuid.uuid4().hex[:8]
    data = {
        "suffix": suffix,
        "tenant_a": uuid.uuid4(),
        "tenant_b": uuid.uuid4(),
        "user_a": uuid.uuid4(),
        "user_b": uuid.uuid4(),
        "slug_a": f"rls-test-a-{suffix}",
        "slug_b": f"rls-test-b-{suffix}",
        "email_a": f"rls-a-{suffix}@example.test",
        "email_b": f"rls-b-{suffix}@example.test",
        "wf_a": f"wf-a-{suffix}",
        "wf_b": f"wf-b-{suffix}",
        "extra_tenants": [],
        "extra_users": [],
    }
    with owner_engine.begin() as conn:
        for side in ("a", "b"):
            conn.execute(
                text(
                    "insert into tenants (id, name, slug, region) "
                    "values (:id, :name, :slug, 'eu')"
                ),
                {"id": data[f"tenant_{side}"], "name": f"RLS {side}", "slug": data[f"slug_{side}"]},
            )
            conn.execute(
                text("insert into users (id, email) values (:id, :email)"),
                {"id": data[f"user_{side}"], "email": data[f"email_{side}"]},
            )
            conn.execute(
                text(
                    "insert into memberships (tenant_id, user_id, role) "
                    "values (:t, :u, 'owner')"
                ),
                {"t": data[f"tenant_{side}"], "u": data[f"user_{side}"]},
            )
            conn.execute(
                text("insert into workflows (tenant_id, name) values (:t, :n)"),
                {"t": data[f"tenant_{side}"], "n": data[f"wf_{side}"]},
            )
        conn.execute(
            text(
                "insert into audit_logs (tenant_id, action, entity_type) "
                "values (:t, 'rls.test', 'test')"
            ),
            {"t": data["tenant_a"]},
        )
    yield data
    with owner_engine.begin() as conn:
        tenants = [data["tenant_a"], data["tenant_b"], *data["extra_tenants"]]
        users = [data["user_a"], data["user_b"], *data["extra_users"]]
        conn.execute(text("delete from tenants where id = any(:ids)"), {"ids": tenants})
        conn.execute(text("delete from users where id = any(:ids)"), {"ids": users})


# --- Fail-closed: tenant set nahi to koi row nahi ---------------------------------


@pytest.mark.parametrize("table", ALL_TABLES)
def test_no_tenant_set_returns_zero_rows(app_engine, seed, table):
    with tenant_conn(app_engine) as conn:
        assert conn.scalar(text(f"select count(*) from {table}")) == 0


# --- Tenant A sirf apna data dekhta hai -------------------------------------------


def test_tenant_a_sees_only_its_workflows(app_engine, seed):
    with tenant_conn(app_engine, tenant_id=seed["tenant_a"]) as conn:
        names = {r[0] for r in conn.execute(text("select name from workflows"))}
    assert names == {seed["wf_a"]}


def test_tenant_b_sees_only_its_workflows(app_engine, seed):
    with tenant_conn(app_engine, tenant_id=seed["tenant_b"]) as conn:
        names = {r[0] for r in conn.execute(text("select name from workflows"))}
    assert names == {seed["wf_b"]}


def test_tenant_a_sees_only_its_own_tenant_row(app_engine, seed):
    with tenant_conn(app_engine, tenant_id=seed["tenant_a"]) as conn:
        slugs = {r[0] for r in conn.execute(text("select slug from tenants"))}
    assert slugs == {seed["slug_a"]}


def test_tenant_context_only_shows_member_users(app_engine, seed):
    with tenant_conn(app_engine, tenant_id=seed["tenant_a"]) as conn:
        emails = {r[0] for r in conn.execute(text("select email from users"))}
    assert emails == {seed["email_a"]}


def test_user_context_sees_self_and_own_tenants_only(app_engine, seed):
    with tenant_conn(app_engine, user_id=seed["user_b"]) as conn:
        emails = {r[0] for r in conn.execute(text("select email from users"))}
        slugs = {r[0] for r in conn.execute(text("select slug from tenants"))}
        memberships = conn.execute(text("select tenant_id from memberships")).all()
        workflows = conn.scalar(text("select count(*) from workflows"))
    assert emails == {seed["email_b"]}
    assert slugs == {seed["slug_b"]}
    assert [m[0] for m in memberships] == [seed["tenant_b"]]
    assert workflows == 0


# --- Cross-tenant likhna band -----------------------------------------------------


def test_cross_tenant_insert_is_rejected(app_engine, seed):
    with pytest.raises(DBAPIError), tenant_conn(app_engine, tenant_id=seed["tenant_a"]) as conn:
        conn.execute(
            text("insert into workflows (tenant_id, name) values (:t, 'evil')"),
            {"t": seed["tenant_b"]},
        )


def test_moving_row_to_other_tenant_is_rejected(app_engine, seed):
    with pytest.raises(DBAPIError), tenant_conn(app_engine, tenant_id=seed["tenant_a"]) as conn:
        conn.execute(
            text("update workflows set tenant_id = :b where tenant_id = :a"),
            {"a": seed["tenant_a"], "b": seed["tenant_b"]},
        )


def test_cannot_create_tenant_row_for_another_id(app_engine, seed):
    with pytest.raises(DBAPIError), tenant_conn(app_engine, tenant_id=seed["tenant_a"]) as conn:
        conn.execute(
            text(
                "insert into tenants (id, name, slug, region) "
                "values (:id, 'x', :slug, 'eu')"
            ),
            {"id": uuid.uuid4(), "slug": f"rls-test-x-{seed['suffix']}"},
        )


def test_cross_tenant_update_and_delete_touch_zero_rows(app_engine, owner_engine, seed):
    with tenant_conn(app_engine, tenant_id=seed["tenant_a"]) as conn:
        upd = conn.execute(
            text("update workflows set name = 'hacked' where tenant_id = :b"),
            {"b": seed["tenant_b"]},
        )
        dele = conn.execute(
            text("delete from workflows where tenant_id = :b"), {"b": seed["tenant_b"]}
        )
        assert upd.rowcount == 0
        assert dele.rowcount == 0
    with owner_engine.connect() as conn:
        name = conn.scalar(
            text("select name from workflows where tenant_id = :b"), {"b": seed["tenant_b"]}
        )
    assert name == seed["wf_b"]


# --- Append-only tables -----------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    ["delete from audit_logs", "update audit_logs set action = 'tampered'"],
)
def test_audit_logs_are_append_only(app_engine, seed, statement):
    with pytest.raises(DBAPIError), tenant_conn(app_engine, tenant_id=seed["tenant_a"]) as conn:
        conn.execute(text(statement))


# --- Supabase Data API roles ------------------------------------------------------


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_data_api_roles_have_no_table_access(owner_engine, role):
    with owner_engine.connect() as conn:
        exists = conn.scalar(text("select count(*) from pg_roles where rolname = :r"), {"r": role})
        if not exists:
            pytest.skip(f"role {role} does not exist")
        leaked = conn.scalar(
            text(
                "select count(*) from information_schema.tables t "
                "where t.table_schema = 'public' and t.table_name <> 'alembic_version' "
                "and has_table_privilege(:r, format('public.%I', t.table_name), "
                "'SELECT,INSERT,UPDATE,DELETE')"
            ),
            {"r": role},
        )
    assert leaked == 0


# --- Pool / transaction behaviour -------------------------------------------------


def test_tenant_setting_does_not_leak_to_next_transaction(app_engine, seed):
    with tenant_conn(app_engine, tenant_id=seed["tenant_a"]) as conn:
        assert conn.scalar(text("select count(*) from workflows")) == 1
    with tenant_conn(app_engine) as conn:
        assert conn.scalar(text("select count(*) from workflows")) == 0


def test_signup_flow_works_under_rls(app_engine, seed):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    seed["extra_tenants"].append(tenant_id)
    seed["extra_users"].append(user_id)
    with tenant_conn(app_engine, tenant_id=tenant_id, user_id=user_id) as conn:
        conn.execute(
            text(
                "insert into tenants (id, name, slug, region) "
                "values (:id, 'Signup Co', :slug, 'eu')"
            ),
            {"id": tenant_id, "slug": f"rls-test-s-{seed['suffix']}"},
        )
        conn.execute(
            text("insert into users (id, email) values (:id, :email)"),
            {"id": user_id, "email": f"rls-s-{seed['suffix']}@example.test"},
        )
        conn.execute(
            text(
                "insert into memberships (tenant_id, user_id, role) "
                "values (:t, :u, 'owner')"
            ),
            {"t": tenant_id, "u": user_id},
        )
        assert conn.scalar(text("select count(*) from memberships")) == 1