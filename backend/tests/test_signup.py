"""Signup (UC-01) aur /me: real DB, opsmind_app role, RLS ON."""

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
CREATED_USERS: list[uuid.UUID] = []


def fake_user(
    x_test_user: Annotated[str | None, Header()] = None,
    x_test_email: Annotated[str | None, Header()] = None,
) -> AuthUser:
    if not x_test_user:
        raise HTTPException(401, "Not authenticated")
    return AuthUser(id=uuid.UUID(x_test_user), email=x_test_email)


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


@pytest.fixture(scope="module", autouse=True)
def _override_auth_and_cleanup(owner_engine):
    app.dependency_overrides[get_current_user] = fake_user
    yield
    app.dependency_overrides.pop(get_current_user, None)
    ids = list(CREATED_USERS)
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "delete from tenants where id in "
                "(select tenant_id from memberships where user_id = any(:ids))"
            ),
            {"ids": ids},
        )
        conn.execute(text("delete from users where id = any(:ids)"), {"ids": ids})


def new_user() -> tuple[uuid.UUID, str]:
    uid = uuid.uuid4()
    CREATED_USERS.append(uid)
    return uid, f"signup-{uid.hex[:8]}@example.test"


def hdr(uid: uuid.UUID, email: str | None = None) -> dict:
    headers = {"X-Test-User": str(uid)}
    if email:
        headers["X-Test-Email"] = email
    return headers


def signup(uid, email, name, region="eu", slug=None):
    body = {"name": name, "region": region}
    if slug:
        body["slug"] = slug
    return client.post("/api/v1/tenants", json=body, headers=hdr(uid, email))


def test_signup_creates_everything(owner_engine):
    uid, email = new_user()
    name = f"Acme {uid.hex[:8]}"
    r = signup(uid, email, name)
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == f"acme-{uid.hex[:8]}"
    assert (body["role"], body["plan"], body["region"]) == ("owner", "free", "eu")

    with owner_engine.connect() as c:
        tid = body["id"]
        member = c.execute(
            text("select role from memberships where user_id = :u and tenant_id = :t"),
            {"u": uid, "t": tid},
        ).scalar_one()
        sub = c.execute(
            text("select plan, status from subscriptions where tenant_id = :t"), {"t": tid}
        ).one()
        audits = c.scalar(
            text(
                "select count(*) from audit_logs where tenant_id = :t "
                "and action = 'tenant.created' and actor_user_id = :u"
            ),
            {"t": tid, "u": uid},
        )
        db_email = c.scalar(text("select email from users where id = :u"), {"u": uid})
    assert member == "owner"
    assert tuple(sub) == ("free", "active")
    assert audits == 1
    assert db_email == email


def test_me_lists_own_company_after_signup():
    uid, email = new_user()
    created = signup(uid, email, f"Beta {uid.hex[:8]}").json()
    r = client.get("/api/v1/me", headers=hdr(uid, email))
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == email
    assert [t["id"] for t in body["tenants"]] == [created["id"]]
    assert body["tenants"][0]["role"] == "owner"


def test_me_before_signup_returns_empty_list():
    uid, email = new_user()
    r = client.get("/api/v1/me", headers=hdr(uid, email))
    assert r.status_code == 200
    assert r.json()["tenants"] == []
    assert r.json()["email"] == email


def test_me_never_shows_other_users_companies():
    (u1, e1), (u2, e2) = new_user(), new_user()
    t1 = signup(u1, e1, f"One {u1.hex[:8]}").json()["id"]
    t2 = signup(u2, e2, f"Two {u2.hex[:8]}").json()["id"]
    ids1 = [t["id"] for t in client.get("/api/v1/me", headers=hdr(u1, e1)).json()["tenants"]]
    ids2 = [t["id"] for t in client.get("/api/v1/me", headers=hdr(u2, e2)).json()["tenants"]]
    assert ids1 == [t1]
    assert ids2 == [t2]


def test_company_limit_is_three():
    uid, email = new_user()
    for i in range(3):
        assert signup(uid, email, f"Co{i} {uid.hex[:8]}").status_code == 201
    r = signup(uid, email, f"Co3 {uid.hex[:8]}")
    assert r.status_code == 409
    assert "limit" in r.json()["detail"].lower()
    assert len(client.get("/api/v1/me", headers=hdr(uid, email)).json()["tenants"]) == 3


def test_explicit_slug_conflict_is_409_and_rolls_back(owner_engine):
    (u1, e1), (u2, e2) = new_user(), new_user()
    slug = f"explicit-{u1.hex[:8]}"
    assert signup(u1, e1, "First", slug=slug).status_code == 201
    r = signup(u2, e2, "Second", slug=slug)
    assert r.status_code == 409
    with owner_engine.connect() as c:
        assert c.scalar(text("select count(*) from tenants where slug = :s"), {"s": slug}) == 1
        # poori transaction rollback: user row bhi nahi bani
        assert c.scalar(text("select count(*) from users where id = :u"), {"u": u2}) == 0


def test_same_name_gets_unique_auto_slug():
    (u1, e1), (u2, e2) = new_user(), new_user()
    name = f"Twin {u1.hex[:8]}"
    s1 = signup(u1, e1, name)
    s2 = signup(u2, e2, name)
    assert s1.status_code == s2.status_code == 201
    assert s1.json()["slug"] != s2.json()["slug"]
    assert s2.json()["slug"].startswith(s1.json()["slug"])


def test_email_registered_to_other_account_is_409(owner_engine):
    other, email = new_user()
    with owner_engine.begin() as c:
        c.execute(
            text("insert into users (id, email) values (:i, :e)"), {"i": other, "e": email}
        )
    uid, _ = new_user()
    r = signup(uid, email, "Clash")
    assert r.status_code == 409
    with owner_engine.connect() as c:
        assert c.scalar(text("select count(*) from users where id = :u"), {"u": uid}) == 0


def test_inactive_user_is_403(owner_engine):
    uid, email = new_user()
    with owner_engine.begin() as c:
        c.execute(
            text("insert into users (id, email, is_active) values (:i, :e, false)"),
            {"i": uid, "e": email},
        )
    assert signup(uid, email, "Nope").status_code == 403
    assert client.get("/api/v1/me", headers=hdr(uid, email)).status_code == 403


def test_token_without_email_is_400_for_new_user():
    uid, _ = new_user()
    assert signup(uid, None, "No Email").status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Valid Name", "region": "mars"},
        {"name": "A", "region": "eu"},
        {"name": "Valid Name", "region": "eu", "slug": "Bad Slug!"},
        {"name": "Valid Name", "region": "eu", "plan": "pro"},
    ],
)
def test_invalid_payload_is_422(body):
    uid, email = new_user()
    r = client.post("/api/v1/tenants", json=body, headers=hdr(uid, email))
    assert r.status_code == 422


def test_unauthenticated_is_401():
    r = client.post("/api/v1/tenants", json={"name": "Valid Name", "region": "eu"})
    assert r.status_code == 401
    assert client.get("/api/v1/me").status_code == 401