from enum import StrEnum

from app.db.enums import Role


class Permission(StrEnum):
    TENANT_READ = "tenant.read"
    TENANT_MANAGE = "tenant.manage"
    BILLING_MANAGE = "billing.manage"
    USAGE_READ = "usage.read"
    MEMBERS_READ = "members.read"
    MEMBERS_MANAGE = "members.manage"
    CONNECTIONS_READ = "connections.read"
    CONNECTIONS_MANAGE = "connections.manage"
    POLICIES_MANAGE = "policies.manage"
    WORKFLOWS_READ = "workflows.read"
    WORKFLOWS_WRITE = "workflows.write"
    WORKFLOWS_PUBLISH = "workflows.publish"
    KNOWLEDGE_READ = "knowledge.read"
    KNOWLEDGE_WRITE = "knowledge.write"
    RUNS_READ = "runs.read"
    RUNS_REPLAY = "runs.replay"
    REVIEWS_READ = "reviews.read"
    REVIEWS_DECIDE = "reviews.decide"
    DASHBOARDS_READ = "dashboards.read"
    API_KEYS_MANAGE = "api_keys.manage"
    AUDIT_READ = "audit.read"


_OWNER = frozenset({Role.OWNER})
_ADMINS = frozenset({Role.OWNER, Role.ADMIN})
_BUILDERS = frozenset({Role.OWNER, Role.ADMIN, Role.BUILDER})
_REVIEWERS = frozenset({Role.OWNER, Role.ADMIN, Role.REVIEWER})
_EVERYONE = frozenset(Role)

PERMISSION_ROLES: dict[Permission, frozenset[Role]] = {
    Permission.TENANT_READ: _EVERYONE,
    Permission.TENANT_MANAGE: _OWNER,
    Permission.BILLING_MANAGE: _OWNER,
    Permission.USAGE_READ: _ADMINS,
    Permission.MEMBERS_READ: _EVERYONE,
    Permission.MEMBERS_MANAGE: _ADMINS,
    Permission.CONNECTIONS_READ: _EVERYONE,
    Permission.CONNECTIONS_MANAGE: _ADMINS,
    Permission.POLICIES_MANAGE: _ADMINS,
    Permission.WORKFLOWS_READ: _EVERYONE,
    Permission.WORKFLOWS_WRITE: _BUILDERS,
    Permission.WORKFLOWS_PUBLISH: _BUILDERS,
    Permission.KNOWLEDGE_READ: _EVERYONE,
    Permission.KNOWLEDGE_WRITE: _BUILDERS,
    Permission.RUNS_READ: _EVERYONE,
    Permission.RUNS_REPLAY: _ADMINS,
    Permission.REVIEWS_READ: _EVERYONE,
    Permission.REVIEWS_DECIDE: _REVIEWERS,
    Permission.DASHBOARDS_READ: _EVERYONE,
    Permission.API_KEYS_MANAGE: _ADMINS,
    Permission.AUDIT_READ: _ADMINS,
}


def has_permission(role: Role, permission: Permission) -> bool:
    return role in PERMISSION_ROLES[permission]