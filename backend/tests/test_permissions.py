"""Permission matrix as a spec (FR-1.3, AC-2). Matrix badalna ho to yahan bhi jaan boojh kar."""

import pytest

from app.core.permissions import PERMISSION_ROLES, Permission, has_permission
from app.db.enums import Role

OWN, A, B, R, V = Role.OWNER, Role.ADMIN, Role.BUILDER, Role.REVIEWER, Role.VIEWER
EVERYONE = {OWN, A, B, R, V}

EXPECTED: dict[Permission, set[Role]] = {
    Permission.TENANT_READ: EVERYONE,
    Permission.TENANT_MANAGE: {OWN},
    Permission.BILLING_MANAGE: {OWN},
    Permission.USAGE_READ: {OWN, A},
    Permission.MEMBERS_READ: EVERYONE,
    Permission.MEMBERS_MANAGE: {OWN, A},
    Permission.CONNECTIONS_READ: EVERYONE,
    Permission.CONNECTIONS_MANAGE: {OWN, A},
    Permission.POLICIES_MANAGE: {OWN, A},
    Permission.WORKFLOWS_READ: EVERYONE,
    Permission.WORKFLOWS_WRITE: {OWN, A, B},
    Permission.WORKFLOWS_PUBLISH: {OWN, A, B},
    Permission.KNOWLEDGE_READ: EVERYONE,
    Permission.KNOWLEDGE_WRITE: {OWN, A, B},
    Permission.RUNS_READ: EVERYONE,
    Permission.RUNS_REPLAY: {OWN, A},
    Permission.REVIEWS_READ: EVERYONE,
    Permission.REVIEWS_DECIDE: {OWN, A, R},
    Permission.DASHBOARDS_READ: EVERYONE,
    Permission.API_KEYS_MANAGE: {OWN, A},
    Permission.AUDIT_READ: {OWN, A},
}


def test_every_permission_is_governed():
    assert set(EXPECTED) == set(Permission)
    assert set(PERMISSION_ROLES) == set(Permission)


@pytest.mark.parametrize("permission", list(Permission))
def test_matrix_matches_spec(permission):
    for role in Role:
        assert has_permission(role, permission) == (role in EXPECTED[permission]), (
            role,
            permission,
        )


def test_owner_has_every_permission():
    assert all(has_permission(Role.OWNER, p) for p in Permission)


def test_viewer_is_read_only():
    granted = [p for p in Permission if has_permission(Role.VIEWER, p)]
    assert granted
    assert all(p.value.endswith(".read") for p in granted)


def test_reviewer_can_decide_but_not_edit_workflows():
    assert has_permission(Role.REVIEWER, Permission.REVIEWS_DECIDE)
    assert not has_permission(Role.REVIEWER, Permission.WORKFLOWS_WRITE)
    assert not has_permission(Role.REVIEWER, Permission.WORKFLOWS_PUBLISH)


def test_builder_cannot_decide_reviews_or_manage_members():
    assert not has_permission(Role.BUILDER, Permission.REVIEWS_DECIDE)
    assert not has_permission(Role.BUILDER, Permission.MEMBERS_MANAGE)
