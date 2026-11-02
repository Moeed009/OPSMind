"""Tenant context dependency (FR-1.5, NFR-6): real DB, opsmind_app role, RLS ON."""

import uuid
from typing import Annotated

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError

from app.api.deps import TenantCtx, get_current_user
from app.core.config import get_settings
from app.core.security import AuthUser
from app.db.models import Workflow

app = FastAPI()


def fake_user(x_test_user: Annotated[str | None, Header()] = None) -> AuthUser:
    if not x_test_user:
        raise HTTPException(401, "Not authenticated")
    return AuthUser(id=uuid.UUID(x_test_user), email=None)


app.dependency_overrides[get_current_user] = fake_user


@app.get("/ctx")
def ctx(tc: TenantCtx) -> dict:
    names = sorted(tc.db.scalars(select(Workflow.name)))
    return {"tenant_id": str(tc.tenant_id), "role": tc.role.value, "workflows": names}


@app.post("/w")
def create_workflow(tc: TenantCtx, name: str) -> dict:
    tc.db.add(Workflow(tenant_id=tc.tenant_id, name=name))
    tc.db.flush()
    return {"ok": True}


@app.post("/w-fail")
def create_workflow_then_fail(tc: TenantCtx, name: str) -> dict:
    tc.db.add(Workflow(tenant_id=tc.tenant_id, name=name))
    tc.db.flush()
    raise HTTPException(400, "boom")


client = TestClient(app)


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
def seed(owner_engine):
    s = uuid.uuid4().hex[:8]
    d = {
        "s": s,
        "tenant_a": uuid.uuid4(),
        "tenant_b": uuid.uuid4(),
        "user_a": uuid.uuid4(),  # builder in A
        "user_b": uuid.uuid4(),  # owner in B
        "orphan": uuid.uuid4(),  # no membership
        "inactive": uuid.uuid4(),  # member of A but is_active = false
    }
    with owner_engine.begin() as conn:
        for side in ("a", "b"):
            conn.execute(
                text(
                    "insert into tenants (id, name, slug, region) "
                    "values (:id, :n, :slug, 'eu')"
                ),
                {"id": d[f"tenant_{side}"], "n": f"Ctx {side}", "slug": f"ctx-test-{side}-{s}"},
            )
            conn.execute(
                text("insert into workflows (tenant_id, name) values (:t, :n)"),
                {"t": d[f"tenant_{side}"], "n": f"seed-{side}-{s}"},
            )
        users = [
            ("user_a", True, "tenant_a", "builder"),
            ("user_b", True, "tenant_b", "owner"),
            ("orphan", True, None, None),
            ("inactive", False, "tenant_a", "viewer"),
        ]
        for key, active, tenant_key, role in users:
            conn.execute(
                text("insert into users (id, email, is_active) values (:id, :e, :a)"),
                {"id": d[key], "e": f"ctx-{key}-{s}@example.test", "a": active},
            )
            if tenant_key:
                conn.execute(
                    text(
                        "insert into memberships (tenant_id, user_id, role) "
                        "values (:t, :u, :r)"
                    ),
                    {"t": d[tenant_key], "u": d[key], "r": role},
                )
    yield d
    with owner_engine.begin() as conn:
        conn.execute(
            text("delete from tenants where id = any(:ids)"),
            {"ids": [d["tenant_a"], d["tenant_b"]]},
        )
        conn.execute(
            text("delete from users where id = any(:ids)"),
            {"ids": [d["user_a"], d["user_b"], d["orphan"], d["inactive"]]},
        )


def hdr(user, tenant=None) -> dict:
    headers = {"X-Test-User": str(user)}
    if tenant is not None:
        headers["X-Tenant-ID"] = str(tenant)
    return headers


def test_member_gets_context(seed):
    r = client.get("/ctx", headers=hdr(seed["user_a"], seed["tenant_a"]))
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == str(seed["tenant_a"])
    assert body["role"] == "builder"


def test_missing_tenant_header_is_400(seed):
    assert client.get("/ctx", headers=hdr(seed["user_a"])).status_code == 400


def test_invalid_tenant_header_is_400(seed):
    r = client.get("/ctx", headers={**hdr(seed["user_a"]), "X-Tenant-ID": "not-a-uuid"})
    assert r.status_code == 400


def test_unauthenticated_is_401(seed):
    r = client.get("/ctx", headers={"X-Tenant-ID": str(seed["tenant_a"])})
    assert r.status_code == 401


def test_non_member_is_403(seed):
    assert client.get("/ctx", headers=hdr(seed["user_b"], seed["tenant_a"])).status_code == 403
    assert client.get("/ctx", headers=hdr(seed["orphan"], seed["tenant_a"])).status_code == 403


def test_unknown_tenant_is_403(seed):
    r = client.get("/ctx", headers=hdr(seed["user_a"], uuid.uuid4()))
    assert r.status_code == 403


def test_inactive_user_is_403(seed):
    r = client.get("/ctx", headers=hdr(seed["inactive"], seed["tenant_a"]))
    assert r.status_code == 403


def test_context_only_shows_own_tenant_data(seed):
    a = client.get("/ctx", headers=hdr(seed["user_a"], seed["tenant_a"])).json()
    b = client.get("/ctx", headers=hdr(seed["user_b"], seed["tenant_b"])).json()
    assert a["workflows"] == [f"seed-a-{seed['s']}"]
    assert b["workflows"] == [f"seed-b-{seed['s']}"]


def test_write_is_committed_and_visible_next_request(seed):
    name = f"created-{seed['s']}"
    headers = hdr(seed["user_a"], seed["tenant_a"])
    assert client.post("/w", params={"name": name}, headers=headers).status_code == 200
    names = client.get("/ctx", headers=headers).json()["workflows"]
    assert name in names
    other = client.get("/ctx", headers=hdr(seed["user_b"], seed["tenant_b"])).json()
    assert name not in other["workflows"]


def test_error_rolls_back_write(seed):
    name = f"rolled-back-{seed['s']}"
    headers = hdr(seed["user_a"], seed["tenant_a"])
    assert client.post("/w-fail", params={"name": name}, headers=headers).status_code == 400
    assert name not in client.get("/ctx", headers=headers).json()["workflows"]