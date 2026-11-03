"""Members + invitations (FR-1.4, AC-2): real DB, opsmind_app role, RLS ON."""

import hashlib
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
TENANTS: list[uuid.UUID] = []
USERS: list[uuid.UUID] = []


def fake_user(
    x_test_user: Annotated[str | None, Header()] = None,
    x_test_email: Annotated[str | None, Header()] = None,
) -> AuthUser:
    if not x_test_user:
        raise HTTPException(401, "Not authenticated")
    return AuthUser(id=uuid.UUID(x_test_user), email=x_test_email)


class World:
    """Owner connection se seed/inspect (RLS bypass); requests opsmind_app se jati hain."""

    def __init__(self, engine):
        self.engine = engine

    def scalar(self, sql, **params):
        with self.engine.connect() as conn:
            return conn.scalar(text(sql), params)

    def row(self, sql, **params):
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params).one()

    def execute(self, sql, **params):
        with self.engine.begin() as conn:
            conn.execute(text(sql), params)

    def new_user_id(self) -> uuid.UUID:
        uid = uuid.uuid4()
        USERS.append(uid)
        return uid

    def user(self, active=True) -> tuple[uuid.UUID, str]:
        uid = self.new_user_id()
        email = f"mi-{uid.hex[:12]}@example.test"
        self.execute(
            "insert into users (id, email, is_active) values (:i, :e, :a)",
            i=uid, e=email, a=active,
        )
        return uid, email

    def company(self, roles=ROLES) -> dict:
        tid = uuid.uuid4()
        TENANTS.append(tid)
        users = {}
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "insert into tenants (id, name, slug, region) "
                    "values (:i, 'MI Co', :s, 'eu')"
                ),
                {"i": tid, "s": f"mi-{tid.hex[:12]}"},
            )
            for role in roles:
                uid = self.new_user_id()
                users[role] = uid
                conn.execute(
                    text("insert into users (id, email) values (:i, :e)"),
                    {"i": uid, "e": f"mi-{uid.hex[:12]}@example.test"},
                )
                conn.execute(
                    text(
                        "insert into memberships (tenant_id, user_id, role) "
                        "values (:t, :u, :r)"
                    ),
                    {"t": tid, "u": uid, "r": role},
                )
        return {"tenant": tid, "users": users}

    def join(self, tenant, uid, role):
        self.execute(
            "insert into memberships (tenant_id, user_id, role) values (:t, :u, :r)",
            t=tenant, u=uid, r=role,
        )

    def add_member(self, tenant, role) -> uuid.UUID:
        uid, _ = self.user()
        self.join(tenant, uid, role)
        return uid

    def role_of(self, tenant, uid):
        return self.scalar(
            "select role from memberships where tenant_id = :t and user_id = :u",
            t=tenant, u=uid,
        )

    def audit(self, tenant, action) -> int:
        return self.scalar(
            "select count(*) from audit_logs where tenant_id = :t and action = :a",
            t=tenant, a=action,
        )


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
def world(owner_engine):
    app.dependency_overrides[get_current_user] = fake_user
    yield World(owner_engine)
    app.dependency_overrides.pop(get_current_user, None)
    with owner_engine.begin() as conn:
        conn.execute(text("delete from tenants where id = any(:ids)"), {"ids": TENANTS})
        conn.execute(text("delete from users where id = any(:ids)"), {"ids": USERS})


@pytest.fixture(scope="module")
def shared(world):
    """Sirf read-only / non-mutating tests ke liye ek common company."""
    return world.company()


def h(uid, tenant=None, email=None) -> dict:
    headers = {"X-Test-User": str(uid)}
    if tenant is not None:
        headers["X-Tenant-ID"] = str(tenant)
    if email is not None:
        headers["X-Test-Email"] = email
    return headers


def new_email() -> str:
    return f"inv-{uuid.uuid4().hex[:12]}@example.test"


def patch_role(actor, tenant, target, role):
    return client.patch(
        f"/api/v1/members/{target}", json={"role": role}, headers=h(actor, tenant)
    )


def remove(actor, tenant, target):
    return client.delete(f"/api/v1/members/{target}", headers=h(actor, tenant))


def invite(actor, tenant, email, role="builder"):
    return client.post(
        "/api/v1/invitations", json={"email": email, "role": role}, headers=h(actor, tenant)
    )


def revoke(actor, tenant, invitation_id):
    return client.delete(f"/api/v1/invitations/{invitation_id}", headers=h(actor, tenant))


def accept(uid, email, token):
    return client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=h(uid, email=email)
    )


# ------------------------------------------------------------------ members


@pytest.mark.parametrize("role", ROLES)
def test_every_role_can_list_members(shared, role):
    r = client.get("/api/v1/members", headers=h(shared["users"][role], shared["tenant"]))
    assert r.status_code == 200
    got = {m["user_id"]: m["role"] for m in r.json()}
    assert got == {str(uid): name for name, uid in shared["users"].items()}


@pytest.mark.parametrize("role", ["builder", "reviewer", "viewer"])
def test_low_roles_cannot_change_or_remove_others(world, shared, role):
    actor, tenant = shared["users"][role], shared["tenant"]
    target = shared["users"]["admin"]
    assert patch_role(actor, tenant, target, "viewer").status_code == 403
    assert remove(actor, tenant, target).status_code == 403
    assert world.role_of(tenant, target) == "admin"


def test_owner_changes_role_and_it_is_audited(world):
    c = world.company(("owner", "viewer"))
    tenant, owner, viewer = c["tenant"], c["users"]["owner"], c["users"]["viewer"]
    r = patch_role(owner, tenant, viewer, "builder")
    assert r.status_code == 200
    assert r.json()["role"] == "builder"
    assert world.role_of(tenant, viewer) == "builder"
    assert world.audit(tenant, "member.role_changed") == 1


def test_admin_can_manage_lower_roles(world):
    c = world.company(("owner", "admin", "viewer", "builder"))
    tenant, users = c["tenant"], c["users"]
    assert patch_role(users["admin"], tenant, users["viewer"], "reviewer").status_code == 200
    assert world.role_of(tenant, users["viewer"]) == "reviewer"
    assert remove(users["admin"], tenant, users["builder"]).status_code == 204
    assert world.role_of(tenant, users["builder"]) is None


@pytest.mark.parametrize(
    ("target_role", "new_role"),
    [("admin", "viewer"), ("owner", "viewer"), ("viewer", "admin"), ("viewer", "owner")],
)
def test_admin_cannot_touch_admins_owners_or_promote(world, target_role, new_role):
    c = world.company(("owner", "admin", "viewer"))
    tenant = c["tenant"]
    targets = {
        "admin": world.add_member(tenant, "admin"),
        "owner": c["users"]["owner"],
        "viewer": c["users"]["viewer"],
    }
    target = targets[target_role]
    assert patch_role(c["users"]["admin"], tenant, target, new_role).status_code == 403
    assert world.role_of(tenant, target) == target_role


@pytest.mark.parametrize("target_role", ["admin", "owner"])
def test_admin_cannot_remove_admins_or_owners(world, target_role):
    c = world.company(("owner", "admin"))
    tenant = c["tenant"]
    target = world.add_member(tenant, "admin") if target_role == "admin" else c["users"]["owner"]
    assert remove(c["users"]["admin"], tenant, target).status_code == 403
    assert world.role_of(tenant, target) == target_role


def test_sole_owner_cannot_demote_or_leave(world):
    c = world.company(("owner", "viewer"))
    tenant, owner = c["tenant"], c["users"]["owner"]
    assert patch_role(owner, tenant, owner, "admin").status_code == 409
    assert remove(owner, tenant, owner).status_code == 409
    assert world.role_of(tenant, owner) == "owner"


def test_owner_can_demote_self_when_another_owner_exists(world):
    c = world.company(("owner",))
    tenant, first = c["tenant"], c["users"]["owner"]
    world.add_member(tenant, "owner")
    assert patch_role(first, tenant, first, "admin").status_code == 200
    assert world.role_of(tenant, first) == "admin"


def test_removed_member_loses_access(world):
    c = world.company(("owner", "builder"))
    tenant, owner, builder = c["tenant"], c["users"]["owner"], c["users"]["builder"]
    assert remove(owner, tenant, builder).status_code == 204
    assert world.role_of(tenant, builder) is None
    assert world.audit(tenant, "member.removed") == 1
    assert client.get("/api/v1/tenant", headers=h(builder, tenant)).status_code == 403


def test_member_can_leave_company(world):
    c = world.company(("owner", "viewer"))
    tenant, viewer = c["tenant"], c["users"]["viewer"]
    assert remove(viewer, tenant, viewer).status_code == 204
    assert world.role_of(tenant, viewer) is None
    assert world.audit(tenant, "member.left") == 1


def test_unknown_member_is_404(world):
    c = world.company(("owner",))
    tenant, owner, ghost = c["tenant"], c["users"]["owner"], uuid.uuid4()
    assert patch_role(owner, tenant, ghost, "viewer").status_code == 404
    assert remove(owner, tenant, ghost).status_code == 404


def test_owner_of_other_company_cannot_manage_members(world, shared):
    other_owner = world.company(("owner",))["users"]["owner"]
    tenant, viewer = shared["tenant"], shared["users"]["viewer"]
    assert patch_role(other_owner, tenant, viewer, "admin").status_code == 403
    assert remove(other_owner, tenant, viewer).status_code == 403
    assert world.role_of(tenant, viewer) == "viewer"


@pytest.mark.parametrize("body", [{"role": "god"}, {"role": "viewer", "extra": 1}, {}])
def test_invalid_role_payload_is_422(shared, body):
    r = client.patch(
        f"/api/v1/members/{shared['users']['viewer']}",
        json=body,
        headers=h(shared["users"]["owner"], shared["tenant"]),
    )
    assert r.status_code == 422


# -------------------------------------------------------------- invitations


def test_owner_creates_invitation_and_token_is_hashed(world):
    c = world.company(("owner",))
    tenant, owner = c["tenant"], c["users"]["owner"]
    email = new_email()
    r = invite(owner, tenant, email, "builder")
    assert r.status_code == 201
    body = r.json()
    token = body["token"]
    assert len(token) >= 32
    assert "token_hash" not in body
    row = world.row(
        "select token_hash, email, role from invitations where id = :i", i=body["id"]
    )
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.token_hash != token
    assert (row.email, row.role) == (email, "builder")
    assert world.audit(tenant, "invitation.created") == 1


@pytest.mark.parametrize(
    ("actor", "role", "expected"),
    [
        ("owner", "admin", 201),
        ("owner", "viewer", 201),
        ("admin", "builder", 201),
        ("admin", "admin", 403),
        ("builder", "viewer", 403),
        ("reviewer", "viewer", 403),
        ("viewer", "viewer", 403),
    ],
)
def test_invite_role_rules(shared, actor, role, expected):
    r = invite(shared["users"][actor], shared["tenant"], new_email(), role)
    assert r.status_code == expected


def test_cannot_invite_as_owner(shared):
    r = invite(shared["users"]["owner"], shared["tenant"], new_email(), "owner")
    assert r.status_code == 422


def test_duplicate_pending_and_existing_member_are_409(world):
    c = world.company(("owner", "viewer"))
    tenant, owner, viewer = c["tenant"], c["users"]["owner"], c["users"]["viewer"]
    email = new_email()
    assert invite(owner, tenant, email).status_code == 201
    assert invite(owner, tenant, email).status_code == 409
    member_email = world.scalar("select email from users where id = :u", u=viewer)
    assert invite(owner, tenant, member_email).status_code == 409


def test_reinvite_after_revoke_is_allowed(world):
    c = world.company(("owner",))
    tenant, owner = c["tenant"], c["users"]["owner"]
    email = new_email()
    first = invite(owner, tenant, email).json()
    assert revoke(owner, tenant, first["id"]).status_code == 204
    assert invite(owner, tenant, email).status_code == 201


def test_reinvite_after_expiry_replaces_stale_invitation(world):
    c = world.company(("owner",))
    tenant, owner = c["tenant"], c["users"]["owner"]
    email = new_email()
    first = invite(owner, tenant, email).json()
    world.execute(
        "update invitations set expires_at = now() - interval '1 day' where id = :i",
        i=first["id"],
    )
    assert invite(owner, tenant, email).status_code == 201
    stale_revoked = world.scalar(
        "select revoked_at is not null from invitations where id = :i", i=first["id"]
    )
    assert stale_revoked is True


def test_pending_list_hides_secrets_and_needs_permission(world):
    c = world.company(("owner", "viewer"))
    tenant, owner, viewer = c["tenant"], c["users"]["owner"], c["users"]["viewer"]
    email = new_email()
    invite(owner, tenant, email)
    r = client.get("/api/v1/invitations", headers=h(owner, tenant))
    assert r.status_code == 200
    items = r.json()
    assert [i["email"] for i in items] == [email]
    assert not {"token", "token_hash"} & set(items[0])
    assert items[0]["expired"] is False
    assert client.get("/api/v1/invitations", headers=h(viewer, tenant)).status_code == 403


def test_revoke_rules(world):
    c = world.company(("owner", "admin"))
    tenant, owner, admin = c["tenant"], c["users"]["owner"], c["users"]["admin"]
    inv = invite(owner, tenant, new_email(), "admin").json()
    assert revoke(admin, tenant, inv["id"]).status_code == 403  # admin invite = Owner ka haq
    assert revoke(owner, tenant, inv["id"]).status_code == 204
    assert revoke(owner, tenant, inv["id"]).status_code == 204  # idempotent
    assert revoke(owner, tenant, uuid.uuid4()).status_code == 404
    assert client.get("/api/v1/invitations", headers=h(owner, tenant)).json() == []


def test_accept_invitation_happy_path(world):
    c = world.company(("owner",))
    tenant, owner = c["tenant"], c["users"]["owner"]
    email = new_email()
    token = invite(owner, tenant, email, "reviewer").json()["token"]
    invitee = world.new_user_id()  # users row abhi nahi: accept ke waqt banegi

    r = accept(invitee, email, token)
    assert r.status_code == 200
    assert r.json()["id"] == str(tenant)
    assert r.json()["role"] == "reviewer"
    assert world.role_of(tenant, invitee) == "reviewer"
    row = world.row(
        "select accepted_at, accepted_by from invitations where tenant_id = :t", t=tenant
    )
    assert row.accepted_at is not None
    assert row.accepted_by == invitee
    assert world.audit(tenant, "invitation.accepted") == 1

    assert client.get("/api/v1/tenant", headers=h(invitee, tenant)).status_code == 200
    me = client.get("/api/v1/me", headers=h(invitee, email=email)).json()
    assert [t["id"] for t in me["tenants"]] == [str(tenant)]
    assert accept(invitee, email, token).status_code == 410  # token dobara nahi chalta


def test_accept_with_wrong_email_is_403_and_changes_nothing(world):
    c = world.company(("owner",))
    tenant, owner = c["tenant"], c["users"]["owner"]
    token = invite(owner, tenant, new_email()).json()["token"]
    stranger, stranger_email = world.user()
    assert accept(stranger, stranger_email, token).status_code == 403
    assert world.role_of(tenant, stranger) is None
    pending = world.scalar(
        "select accepted_at is null from invitations where tenant_id = :t", t=tenant
    )
    assert pending is True


def test_accept_with_unknown_token_is_404(world):
    uid, email = world.user()
    assert accept(uid, email, "x" * 40).status_code == 404


def test_accept_expired_invitation_is_410(world):
    c = world.company(("owner",))
    tenant, owner = c["tenant"], c["users"]["owner"]
    email = new_email()
    token = invite(owner, tenant, email).json()["token"]
    world.execute(
        "update invitations set expires_at = now() - interval '1 day' where tenant_id = :t",
        t=tenant,
    )
    assert accept(world.new_user_id(), email, token).status_code == 410


def test_revoked_invitation_cannot_be_accepted(world):
    c = world.company(("owner",))
    tenant, owner = c["tenant"], c["users"]["owner"]
    email = new_email()
    body = invite(owner, tenant, email).json()
    assert revoke(owner, tenant, body["id"]).status_code == 204
    assert accept(world.new_user_id(), email, body["token"]).status_code == 410


def test_accept_respects_company_limit(world):
    c = world.company(("owner",))
    tenant, owner = c["tenant"], c["users"]["owner"]
    invitee, email = world.user()
    for _ in range(3):
        world.join(world.company(("owner",))["tenant"], invitee, "viewer")
    token = invite(owner, tenant, email).json()["token"]
    assert accept(invitee, email, token).status_code == 409
    assert world.role_of(tenant, invitee) is None
    pending = world.scalar(
        "select accepted_at is null from invitations where tenant_id = :t", t=tenant
    )
    assert pending is True  # transaction rollback: invite consume nahi hui


def test_accept_when_already_member_is_409(world):
    c = world.company(("owner",))
    tenant, owner = c["tenant"], c["users"]["owner"]
    invitee, email = world.user()
    token = invite(owner, tenant, email).json()["token"]
    world.join(tenant, invitee, "viewer")
    assert accept(invitee, email, token).status_code == 409


def test_accept_requires_authentication(world):
    r = client.post("/api/v1/invitations/accept", json={"token": "x" * 40})
    assert r.status_code == 401


def test_invitations_are_isolated_between_companies(world):
    a = world.company(("owner",))
    b = world.company(("owner",))
    invite(a["users"]["owner"], a["tenant"], new_email())
    r = client.get("/api/v1/invitations", headers=h(b["users"]["owner"], b["tenant"]))
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_invitation_lookup_function_is_not_exposed(world, role):
    if not world.scalar("select count(*) from pg_roles where rolname = :r", r=role):
        pytest.skip(f"role {role} does not exist")
    allowed = world.scalar(
        "select has_function_privilege(cast(:r as name), 'app_find_invitation(text)', 'execute')",
        r=role,
    )
    assert allowed is False