
import uuid
from typing import Annotated

import pytest
from fastapi import Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.security import AuthUser
from app.main import app

client = TestClient(app)
ROLES = ["owner", "admin", "builder", "reviewer", "viewer"]
NON_OWNERS = [r for r in ROLES if r != "owner"]


def fake_user(x_test_user: Annotated[str | None, Header()] = None) -> AuthUser:
    if not x_test_user:
        raise HTTPException(401, "Not authenticated")
    return AuthUser(id=uuid.UUID(x_test_user), email=None)


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
    tag = uuid.uuid4().hex[:8]
    data = {
        "tag": tag,
        "tenant": uuid.uuid4(),
        "other_tenant": uuid.uuid4(),
        "other_owner": uuid.uuid4(),
        "users": {role: uuid.uuid4() for role in ROLES},
    }
    with owner_engine.begin() as conn:
        for key, slug, name in (
            ("tenant", f"rbac-main-{tag}", f"RBAC Main {tag}"),
            ("other_tenant", f"rbac-other-{tag}", f"RBAC Other {tag}"),
        ):
            conn.execute(
                text(
                    "insert into tenants (id, name, slug, region) "
                    "values (:i, :n, :s, 'eu')"
                ),
                {"i": data[key], "n": name, "s": slug},
            )
        members = [(uid, role, data["tenant"]) for role, uid in data["users"].items()]
        members.append((data["other_owner"], "owner", data["other_tenant"]))
        for uid, role, tenant_id in members:
            conn.execute(
                text("insert into users (id, email) values (:i, :e)"),
                {"i": uid, "e": f"rbac-{uid.hex[:12]}@example.test"},
            )
            conn.execute(
                text(
                    "insert into memberships (tenant_id, user_id, role) values (:t, :u, :r)"
                ),
                {"t": tenant_id, "u": uid, "r": role},
            )
    app.dependency_overrides[get_current_user] = fake_user
    yield data
    app.dependency_overrides.pop(get_current_user, None)
    user_ids = [*data["users"].values(), data["other_owner"]]
    with owner_engine.begin() as conn:
        conn.execute(
            text("delete from tenants where id = any(:ids)"),
            {"ids": [data["tenant"], data["other_tenant"]]},
        )
        conn.execute(text("delete from users where id = any(:ids)"), {"ids": user_ids})


def hdr(user_id, tenant_id) -> dict:
    return {"X-Test-User": str(user_id), "X-Tenant-ID": str(tenant_id)}


def db_name(engine, tenant_id) -> str:
    with engine.connect() as conn:
        return conn.scalar(text("select name from tenants where id = :t"), {"t": tenant_id})


def audit_count(engine, tenant_id) -> int:
    with engine.connect() as conn:
        return conn.scalar(
            text(
                "select count(*) from audit_logs "
                "where tenant_id = :t and action = 'tenant.updated'"
            ),
            {"t": tenant_id},
        )


@pytest.mark.parametrize("role", ROLES)
def test_every_role_can_read_the_tenant(seed, role):
    r = client.get("/api/v1/tenant", headers=hdr(seed["users"][role], seed["tenant"]))
    assert r.status_code == 200
    assert r.json()["role"] == role
    assert r.json()["id"] == str(seed["tenant"])


@pytest.mark.parametrize("role", NON_OWNERS)
def test_non_owner_cannot_rename_tenant(seed, owner_engine, role):
    before = db_name(owner_engine, seed["tenant"])
    audits = audit_count(owner_engine, seed["tenant"])
    r = client.patch(
        "/api/v1/tenant",
        json={"name": f"Hacked by {role}"},
        headers=hdr(seed["users"][role], seed["tenant"]),
    )
    assert r.status_code == 403
    assert db_name(owner_engine, seed["tenant"]) == before
    assert audit_count(owner_engine, seed["tenant"]) == audits


def test_owner_can_rename_and_it_is_audited(seed, owner_engine):
    headers = hdr(seed["users"]["owner"], seed["tenant"])
    old = db_name(owner_engine, seed["tenant"])
    audits = audit_count(owner_engine, seed["tenant"])
    new = f"Renamed {seed['tag']}"

    r = client.patch("/api/v1/tenant", json={"name": new}, headers=headers)
    assert r.status_code == 200
    assert r.json()["name"] == new
    assert client.get("/api/v1/tenant", headers=headers).json()["name"] == new
    assert db_name(owner_engine, seed["tenant"]) == new
    assert audit_count(owner_engine, seed["tenant"]) == audits + 1

    with owner_engine.connect() as conn:
        details = conn.scalar(
            text(
                "select details from audit_logs where tenant_id = :t "
                "and action = 'tenant.updated' order by created_at desc limit 1"
            ),
            {"t": seed["tenant"]},
        )
    assert details == {"name": {"old": old, "new": new}}


def test_renaming_to_same_name_writes_no_audit_entry(seed, owner_engine):
    headers = hdr(seed["users"]["owner"], seed["tenant"])
    current = db_name(owner_engine, seed["tenant"])
    audits = audit_count(owner_engine, seed["tenant"])
    r = client.patch("/api/v1/tenant", json={"name": current}, headers=headers)
    assert r.status_code == 200
    assert audit_count(owner_engine, seed["tenant"]) == audits


def test_owner_of_another_company_cannot_touch_this_one(seed, owner_engine):
    before = db_name(owner_engine, seed["tenant"])
    headers = hdr(seed["other_owner"], seed["tenant"])
    assert client.get("/api/v1/tenant", headers=headers).status_code == 403
    r = client.patch("/api/v1/tenant", json={"name": "Stolen"}, headers=headers)
    assert r.status_code == 403
    assert db_name(owner_engine, seed["tenant"]) == before


@pytest.mark.parametrize("body", [{"name": "A"}, {"name": "Valid Name", "region": "us"}, {}])
def test_invalid_rename_payload_is_422(seed, body):
    headers = hdr(seed["users"]["owner"], seed["tenant"])
    assert client.patch("/api/v1/tenant", json=body, headers=headers).status_code == 422