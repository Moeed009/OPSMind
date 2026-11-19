"""Department Management, RBAC Enforcement, Member Assignment, and Operational Dashboard Service."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import Permission, has_permission
from app.db.enums import (
    ConnectionStatus,
    DepartmentRole,
    DepartmentStatus,
    DepartmentType,
    Role,
    RunStatus,
    UsageMetric,
    WorkflowVersionStatus,
)
from app.db.models import (
    AuditLog,
    Connection,
    Department,
    DepartmentMember,
    Membership,
    Run,
    Tenant,
    UsageRecord,
    User,
    Workflow,
    WorkflowVersion,
)
from app.schemas.department import (
    DepartmentConnectionItem,
    DepartmentCreate,
    DepartmentDashboardOut,
    DepartmentMemberOut,
    DepartmentOperationalMetrics,
    DepartmentOut,
    DepartmentRunItem,
    DepartmentTemplateOut,
    DepartmentUpdate,
    DepartmentWorkflowItem,
)

DEPARTMENT_TEMPLATES: dict[str, dict[str, Any]] = {
    "support": {
        "name": "Customer Support",
        "code": "support",
        "description": "Triage incoming customer emails, retrieve orders and KB help docs, draft replies, and route high-risk approvals.",
        "recommended_providers": ["gmail", "hubspot", "slack"],
        "default_settings": {"require_human_review": True, "auto_triage_enabled": True},
    },
    "finance": {
        "name": "Finance",
        "code": "finance",
        "description": "Extract supplier invoice fields, verify against purchase orders, route mismatches for human approval, and create invoice records.",
        "recommended_providers": ["gmail", "google_drive", "zoho_invoice"],
        "default_settings": {"require_human_review": True, "financial_policy_strict": True},
    },
    "sales": {
        "name": "Sales",
        "code": "sales",
        "description": "Qualify inbound leads, extract requirements, check CRM duplicates, draft follow-ups, and update HubSpot & Calendar.",
        "recommended_providers": ["gmail", "hubspot", "google_calendar"],
        "default_settings": {"require_human_review": False, "auto_triage_enabled": True},
    },
    "legal": {
        "name": "Legal",
        "code": "legal",
        "description": "Review Drive contracts, extract terms, check company policies & standard clauses, and require mandatory human approval.",
        "recommended_providers": ["google_drive", "gmail", "slack"],
        "default_settings": {"require_human_review": True, "mandatory_human_approval": True},
    },
    "operations": {
        "name": "Internal Operations",
        "code": "operations",
        "description": "Generate weekly aggregate operational reports and answer employee policy questions with verified sources.",
        "recommended_providers": ["slack", "google_drive", "gmail"],
        "default_settings": {"require_human_review": False, "auto_triage_enabled": True},
    },
}


def list_templates() -> list[DepartmentTemplateOut]:
    return [
        DepartmentTemplateOut(
            key=k,
            name=v["name"],
            code=v["code"],
            description=v["description"],
            recommended_providers=v["recommended_providers"],
            default_settings=v["default_settings"],
        )
        for k, v in DEPARTMENT_TEMPLATES.items()
    ]


def _slugify_code(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:50] or "dept"


def get_assigned_department_ids(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> set[uuid.UUID]:
    rows = db.scalars(
        select(DepartmentMember.department_id).where(
            DepartmentMember.tenant_id == tenant_id,
            DepartmentMember.user_id == user_id,
        )
    ).all()
    return set(rows)


def is_company_admin(role: Role) -> bool:
    return role in (Role.OWNER, Role.ADMIN)


def get_effective_department_role(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
) -> Role | None:
    """Single source of truth for resolving a user's effective role inside a department.

    - Tenant Owner / Admin always retain their tenant role across all departments.
    - Other users must be assigned to the department; their effective role is
      department_members.role if set, otherwise their tenant role.
    """
    if is_company_admin(tenant_role):
        return tenant_role
    dm = db.scalar(
        select(DepartmentMember).where(
            DepartmentMember.tenant_id == tenant_id,
            DepartmentMember.department_id == department_id,
            DepartmentMember.user_id == user_id,
        )
    )
    if dm is None:
        return None
    if dm.role is not None:
        return Role(dm.role.value)
    return tenant_role


def require_department_permission(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    permission: Permission,
    require_active: bool = False,
) -> tuple[Department, Role]:
    """Enforces department membership and permission via get_effective_department_role."""
    dept = db.scalar(
        select(Department).where(
            Department.tenant_id == tenant_id,
            Department.id == department_id,
        )
    )
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")

    eff_role = get_effective_department_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
    )
    if eff_role is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Access denied: you are not assigned to this department",
        )
    if not has_permission(eff_role, permission):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your role does not allow this action in this department",
        )
    if require_active and not is_company_admin(tenant_role) and dept.status != DepartmentStatus.ACTIVE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot modify resources in a {dept.status.value} department",
        )
    return dept, eff_role


def check_department_access(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: Role,
    department_id: uuid.UUID,
    require_write: bool = False,
) -> Department:
    """Enforces tenant and department RBAC rules using get_effective_department_role."""
    dept = db.scalar(
        select(Department).where(
            Department.tenant_id == tenant_id,
            Department.id == department_id,
        )
    )
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")

    eff_role = get_effective_department_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=role,
        department_id=department_id,
    )
    if eff_role is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Access denied: you are not assigned to this department",
        )

    if is_company_admin(role):
        return dept

    if require_write and eff_role not in (Role.OWNER, Role.ADMIN, Role.BUILDER):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your role does not allow modifying department resources",
        )

    if require_write and dept.status != DepartmentStatus.ACTIVE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot modify resources in a {dept.status.value} department",
        )

    return dept


def _enrich_department(
    db: Session,
    tenant_id: uuid.UUID,
    dept: Department,
    *,
    user_id: uuid.UUID | None = None,
    tenant_role: Role | None = None,
) -> DepartmentOut:
    member_count = int(
        db.scalar(
            select(func.count(DepartmentMember.id)).where(
                DepartmentMember.tenant_id == tenant_id,
                DepartmentMember.department_id == dept.id,
            )
        )
        or 0
    )
    connection_count = int(
        db.scalar(
            select(func.count(Connection.id)).where(
                Connection.tenant_id == tenant_id,
                Connection.department_id == dept.id,
                Connection.status != ConnectionStatus.REVOKED,
            )
        )
        or 0
    )
    workflow_count = int(
        db.scalar(
            select(func.count(Workflow.id)).where(
                Workflow.tenant_id == tenant_id,
                Workflow.department_id == dept.id,
                Workflow.archived_at.is_(None),
            )
        )
        or 0
    )
    active_wf_count = int(
        db.scalar(
            select(func.count(func.distinct(Workflow.id)))
            .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
            .where(
                Workflow.tenant_id == tenant_id,
                Workflow.department_id == dept.id,
                Workflow.archived_at.is_(None),
                WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
            )
        )
        or 0
    )
    my_eff_role = (
        get_effective_department_role(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_role=tenant_role,
            department_id=dept.id,
        )
        if (user_id is not None and tenant_role is not None)
        else None
    )
    return DepartmentOut(
        id=dept.id,
        tenant_id=dept.tenant_id,
        name=dept.name,
        code=dept.code,
        description=dept.description,
        template_key=dept.template_key,
        department_type=dept.department_type,
        status=dept.status,
        settings=dept.settings or {},
        monthly_ai_cost_cap_usd=dept.monthly_ai_cost_cap_usd,
        timezone=dept.timezone,
        member_count=member_count,
        connection_count=connection_count,
        workflow_count=workflow_count,
        active_workflow_count=active_wf_count,
        my_effective_role=my_eff_role,
        created_at=dept.created_at,
        updated_at=dept.updated_at,
        archived_at=dept.archived_at,
    )


def _validate_cost_cap(db: Session, tenant_id: uuid.UUID, cap: Decimal | None) -> None:
    if cap is None:
        return
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant and tenant.monthly_ai_cost_cap_usd is not None:
        if cap > tenant.monthly_ai_cost_cap_usd:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Department AI cost cap (${cap:.2f}) cannot exceed company monthly entitlement (${tenant.monthly_ai_cost_cap_usd:.2f}).",
            )


def list_departments(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: Role,
    include_archived: bool = False,
) -> list[DepartmentOut]:
    stmt = select(Department).where(Department.tenant_id == tenant_id)
    if not include_archived:
        stmt = stmt.where(Department.status != DepartmentStatus.ARCHIVED)

    if not is_company_admin(role):
        assigned = get_assigned_department_ids(db, tenant_id, user_id)
        if not assigned:
            return []
        stmt = stmt.where(Department.id.in_(assigned))

    stmt = stmt.order_by(Department.created_at.asc(), Department.name.asc())
    depts = db.scalars(stmt).all()
    return [
        _enrich_department(db, tenant_id, d, user_id=user_id, tenant_role=role)
        for d in depts
    ]


def create_department(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_role: Role,
    payload: DepartmentCreate,
) -> DepartmentOut:
    if not is_company_admin(actor_role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only Owner or Admin can create departments")

    tpl = DEPARTMENT_TEMPLATES.get((payload.template_key or "").lower().strip())
    code = payload.code or (tpl["code"] if tpl else _slugify_code(payload.name))
    description = payload.description if payload.description is not None else (tpl["description"] if tpl else None)
    merged_settings = {**(tpl["default_settings"] if tpl else {}), **(payload.settings or {})}

    _validate_cost_cap(db, tenant_id, payload.monthly_ai_cost_cap_usd)

    dup_code = db.scalar(
        select(Department.id).where(Department.tenant_id == tenant_id, Department.code == code)
    )
    if dup_code:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A department with code '{code}' already exists in this company.",
        )

    dup_name = db.scalar(
        select(Department.id).where(Department.tenant_id == tenant_id, Department.name == payload.name)
    )
    if dup_name:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A department named '{payload.name}' already exists in this company.",
        )

    tpl_key = payload.template_key.lower().strip() if payload.template_key else None
    resolved_type = payload.department_type
    if resolved_type is None:
        if tpl_key and tpl_key in {t.value for t in DepartmentType}:
            resolved_type = DepartmentType(tpl_key)
        else:
            resolved_type = DepartmentType.OPERATIONS

    dept = Department(
        tenant_id=tenant_id,
        name=payload.name,
        code=code,
        description=description,
        template_key=tpl_key,
        department_type=resolved_type,
        status=DepartmentStatus.ACTIVE,
        settings=merged_settings,
        monthly_ai_cost_cap_usd=payload.monthly_ai_cost_cap_usd,
        timezone=payload.timezone,
        created_by=actor_id,
    )
    db.add(dept)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Department name or code conflicts with an existing department in this company.",
        ) from exc

    return _enrich_department(db, tenant_id, dept)


def update_department(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_role: Role,
    department_id: uuid.UUID,
    payload: DepartmentUpdate,
) -> DepartmentOut:
    if not is_company_admin(actor_role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only Owner or Admin can update departments")

    dept = db.scalar(
        select(Department).where(Department.tenant_id == tenant_id, Department.id == department_id)
    )
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")

    if payload.name is not None and payload.name != dept.name:
        dup = db.scalar(
            select(Department.id).where(
                Department.tenant_id == tenant_id,
                Department.name == payload.name,
                Department.id != department_id,
            )
        )
        if dup:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"A department named '{payload.name}' already exists in this company.",
            )
        dept.name = payload.name

    if payload.description is not None:
        dept.description = payload.description
    if payload.department_type is not None:
        dept.department_type = payload.department_type
    if payload.settings is not None:
        dept.settings = payload.settings
    if payload.clear_cost_cap:
        dept.monthly_ai_cost_cap_usd = None
    elif payload.monthly_ai_cost_cap_usd is not None:
        _validate_cost_cap(db, tenant_id, payload.monthly_ai_cost_cap_usd)
        dept.monthly_ai_cost_cap_usd = payload.monthly_ai_cost_cap_usd
    if payload.timezone is not None:
        dept.timezone = payload.timezone

    db.flush()
    return _enrich_department(db, tenant_id, dept)


def _pause_department_workflows(db: Session, tenant_id: uuid.UUID, department_id: uuid.UUID) -> int:
    """Safely pauses all published workflow versions in a department when deactivated or archived."""
    wf_ids = list(
        db.scalars(
            select(Workflow.id).where(
                Workflow.tenant_id == tenant_id,
                Workflow.department_id == department_id,
            )
        ).all()
    )
    if not wf_ids:
        return 0
    pub_versions = db.scalars(
        select(WorkflowVersion).where(
            WorkflowVersion.tenant_id == tenant_id,
            WorkflowVersion.workflow_id.in_(wf_ids),
            WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
        )
    ).all()
    for ver in pub_versions:
        ver.status = WorkflowVersionStatus.PAUSED
        ver.pause_reason = "department deactivated or archived"
    db.flush()
    return len(pub_versions)


def _definition_uses_provider(definition: Any, provider: str) -> bool:
    if not isinstance(definition, dict):
        return False
    prefix = f"{provider}."
    trigger = definition.get("trigger") or {}
    if isinstance(trigger, dict) and str(trigger.get("source") or "").lower().startswith(prefix):
        return True
    for node in definition.get("nodes") or []:
        if isinstance(node, dict) and str(node.get("action") or "").lower().startswith(prefix):
            return True
    return False


def pause_workflows_for_connection(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    reason: str = "connection disconnected",
    actor_id: uuid.UUID | None = None,
) -> list[dict[str, str]]:
    """Pauses all published workflow versions referencing `connection_id` (trigger or node config)."""
    rows = db.execute(
        select(WorkflowVersion, Workflow)
        .join(Workflow, WorkflowVersion.workflow_id == Workflow.id)
        .where(
            WorkflowVersion.tenant_id == tenant_id,
            WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
        )
    ).all()

    conn_str = str(connection_id)
    conn = db.scalar(
        select(Connection).where(Connection.tenant_id == tenant_id, Connection.id == connection_id)
    )
    provider = (conn.provider or "").lower() if conn else ""
    conn_dept_id = conn.department_id if conn else None

    paused: list[dict[str, str]] = []
    for ver, wf in rows:
        matches_trigger = ver.trigger_connection_id == connection_id
        matches_node = False
        if not matches_trigger and isinstance(ver.definition, dict):
            for node in ver.definition.get("nodes") or []:
                if isinstance(node, dict):
                    cfg = node.get("config") or {}
                    if isinstance(cfg, dict) and str(cfg.get("connection_id") or "") == conn_str:
                        matches_node = True
                        break
        matches_provider = bool(
            provider
            and conn_dept_id is not None
            and wf.department_id == conn_dept_id
            and _definition_uses_provider(ver.definition, provider)
        )
        if not (matches_trigger or matches_node or matches_provider):
            continue

        ver.status = WorkflowVersionStatus.PAUSED
        ver.pause_reason = reason
        db.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                action="workflow.auto_paused",
                entity_type="workflow_version",
                entity_id=str(ver.id),
                details={
                    "workflow_id": str(wf.id),
                    "workflow_name": wf.name,
                    "connection_id": conn_str,
                    "reason": reason,
                },
            )
        )
        paused.append(
            {
                "workflow_id": str(wf.id),
                "workflow_name": wf.name,
                "version_id": str(ver.id),
            }
        )
    if paused:
        db.flush()
    return paused


def set_department_status(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_role: Role,
    department_id: uuid.UUID,
    new_status: DepartmentStatus,
) -> DepartmentOut:
    if not is_company_admin(actor_role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only Owner or Admin can change department status")

    dept = db.scalar(
        select(Department).where(Department.tenant_id == tenant_id, Department.id == department_id)
    )
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")

    dept.status = new_status
    if new_status == DepartmentStatus.ARCHIVED:
        dept.archived_at = datetime.now(UTC)
        _pause_department_workflows(db, tenant_id, department_id)
    elif new_status == DepartmentStatus.INACTIVE:
        _pause_department_workflows(db, tenant_id, department_id)
    elif new_status == DepartmentStatus.ACTIVE:
        dept.archived_at = None

    db.flush()
    return _enrich_department(db, tenant_id, dept)


def list_department_members(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: Role,
    department_id: uuid.UUID,
) -> list[DepartmentMemberOut]:
    check_department_access(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        department_id=department_id,
        require_write=False,
    )
    rows = db.execute(
        select(
            DepartmentMember.user_id,
            User.email,
            User.full_name,
            Membership.role,
            DepartmentMember.role.label("department_role"),
            DepartmentMember.assigned_at,
        )
        .join(User, User.id == DepartmentMember.user_id)
        .join(
            Membership,
            (Membership.user_id == DepartmentMember.user_id)
            & (Membership.tenant_id == DepartmentMember.tenant_id),
        )
        .where(
            DepartmentMember.tenant_id == tenant_id,
            DepartmentMember.department_id == department_id,
        )
        .order_by(DepartmentMember.assigned_at.asc(), User.email.asc())
    ).all()
    out: list[DepartmentMemberOut] = []
    for r in rows:
        dept_role = r.department_role
        eff_role = Role(dept_role.value) if dept_role is not None else r.role
        out.append(
            DepartmentMemberOut(
                user_id=r.user_id,
                email=r.email,
                full_name=r.full_name,
                role=r.role,
                department_role=dept_role,
                effective_role=eff_role,
                assigned_at=r.assigned_at,
            )
        )
    return out


def _count_other_department_admins(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    exclude_user_id: uuid.UUID,
) -> int:
    count = db.scalar(
        select(func.count(DepartmentMember.id))
        .join(
            Membership,
            (Membership.user_id == DepartmentMember.user_id)
            & (Membership.tenant_id == DepartmentMember.tenant_id),
        )
        .where(
            DepartmentMember.tenant_id == tenant_id,
            DepartmentMember.department_id == department_id,
            DepartmentMember.user_id != exclude_user_id,
            (DepartmentMember.role == DepartmentRole.ADMIN)
            | (DepartmentMember.role.is_(None) & (Membership.role == Role.ADMIN)),
        )
    )
    return int(count or 0)


def assign_members_to_department(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_role: Role,
    department_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    role: DepartmentRole | None = None,
) -> list[DepartmentMemberOut]:
    if not is_company_admin(actor_role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only Owner or Admin can assign department members")

    dept = db.scalar(
        select(Department).where(Department.tenant_id == tenant_id, Department.id == department_id)
    )
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    if dept.status == DepartmentStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot assign members to an archived department")

    unique_uids = list(dict.fromkeys(user_ids))
    company_member_uids = set(
        db.scalars(
            select(Membership.user_id).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id.in_(unique_uids),
            )
        ).all()
    )
    missing = [str(uid) for uid in unique_uids if uid not in company_member_uids]
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"User(s) are not members of this company: {', '.join(missing)}",
        )

    existing_uids = set(
        db.scalars(
            select(DepartmentMember.user_id).where(
                DepartmentMember.tenant_id == tenant_id,
                DepartmentMember.department_id == department_id,
                DepartmentMember.user_id.in_(unique_uids),
            )
        ).all()
    )
    if existing_uids:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "User is already assigned to this department.",
        )

    for uid in unique_uids:
        db.add(
            DepartmentMember(
                tenant_id=tenant_id,
                department_id=department_id,
                user_id=uid,
                role=role,
                assigned_by=actor_id,
            )
        )
        db.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                action="department_member.assigned",
                entity_type="department_member",
                entity_id=str(uid),
                details={
                    "department_id": str(department_id),
                    "user_id": str(uid),
                    "old_role": None,
                    "new_role": role.value if role else None,
                },
            )
        )
    db.flush()
    return list_department_members(
        db,
        tenant_id=tenant_id,
        user_id=actor_id,
        role=actor_role,
        department_id=department_id,
    )


def update_department_member_role(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_role: Role,
    department_id: uuid.UUID,
    target_user_id: uuid.UUID,
    new_role: DepartmentRole,
) -> DepartmentMemberOut:
    dept = db.scalar(
        select(Department).where(Department.tenant_id == tenant_id, Department.id == department_id)
    )
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    if dept.status == DepartmentStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot modify members in an archived department")

    if not is_company_admin(actor_role):
        eff_actor_role = get_effective_department_role(
            db,
            tenant_id=tenant_id,
            user_id=actor_id,
            tenant_role=actor_role,
            department_id=department_id,
        )
        if eff_actor_role != Role.ADMIN:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only company Owner/Admin or this department's Admin can update member roles",
            )

    dm = db.scalar(
        select(DepartmentMember)
        .where(
            DepartmentMember.tenant_id == tenant_id,
            DepartmentMember.department_id == department_id,
            DepartmentMember.user_id == target_user_id,
        )
        .with_for_update()
    )
    if dm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member is not assigned to this department")

    target_membership = db.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == target_user_id,
        )
    )
    target_tenant_role = target_membership.role if target_membership else Role.VIEWER
    is_currently_dept_admin = (
        dm.role == DepartmentRole.ADMIN
        or (dm.role is None and target_tenant_role == Role.ADMIN)
    )
    if is_currently_dept_admin and new_role != DepartmentRole.ADMIN:
        other_admins = _count_other_department_admins(
            db,
            tenant_id=tenant_id,
            department_id=department_id,
            exclude_user_id=target_user_id,
        )
        if other_admins == 0:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A department must keep at least one department admin.",
            )

    old_role = dm.role.value if dm.role is not None else None
    dm.role = new_role
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            action="department_member.role_changed",
            entity_type="department_member",
            entity_id=str(target_user_id),
            details={
                "department_id": str(department_id),
                "user_id": str(target_user_id),
                "old_role": old_role,
                "new_role": new_role.value,
            },
        )
    )
    db.flush()

    members = list_department_members(
        db,
        tenant_id=tenant_id,
        user_id=actor_id,
        role=actor_role,
        department_id=department_id,
    )
    return next(m for m in members if m.user_id == target_user_id)


def remove_member_from_department(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_role: Role,
    department_id: uuid.UUID,
    target_user_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> None:
    if not is_company_admin(actor_role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only Owner or Admin can remove department members")

    dm = db.scalar(
        select(DepartmentMember)
        .where(
            DepartmentMember.tenant_id == tenant_id,
            DepartmentMember.department_id == department_id,
            DepartmentMember.user_id == target_user_id,
        )
        .with_for_update()
    )
    if dm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member is not assigned to this department")

    target_membership = db.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == target_user_id,
        )
    )
    target_tenant_role = target_membership.role if target_membership else Role.VIEWER
    is_currently_dept_admin = (
        dm.role == DepartmentRole.ADMIN
        or (dm.role is None and target_tenant_role == Role.ADMIN)
    )
    if is_currently_dept_admin:
        other_admins = _count_other_department_admins(
            db,
            tenant_id=tenant_id,
            department_id=department_id,
            exclude_user_id=target_user_id,
        )
        if other_admins == 0:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A department must keep at least one department admin.",
            )

    old_role = dm.role.value if dm.role is not None else None
    db.delete(dm)
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            action="department_member.removed",
            entity_type="department_member",
            entity_id=str(target_user_id),
            details={
                "department_id": str(department_id),
                "user_id": str(target_user_id),
                "old_role": old_role,
                "new_role": None,
            },
        )
    )
    db.flush()


def get_department_dashboard(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: Role,
    department_id: uuid.UUID,
) -> DepartmentDashboardOut:
    dept = check_department_access(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        department_id=department_id,
        require_write=False,
    )
    dept_out = _enrich_department(db, tenant_id, dept, user_id=user_id, tenant_role=role)
    members = list_department_members(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        department_id=department_id,
    )

    conn_rows = db.scalars(
        select(Connection)
        .where(
            Connection.tenant_id == tenant_id,
            Connection.department_id == department_id,
            Connection.status != ConnectionStatus.REVOKED,
        )
        .order_by(Connection.created_at.desc())
    ).all()
    connections = [
        DepartmentConnectionItem(
            id=c.id,
            provider=c.provider,
            display_name=c.display_name,
            external_account_id=c.external_account_id,
            status="CONNECTED" if c.status == ConnectionStatus.HEALTHY else c.status.value.upper(),
            department_id=c.department_id,
            last_error=c.last_error,
            last_synced_at=c.last_health_check_at or c.updated_at,
        )
        for c in conn_rows
    ]
    healthy_conns = sum(1 for c in conn_rows if c.status == ConnectionStatus.HEALTHY)

    wf_rows = db.scalars(
        select(Workflow)
        .where(
            Workflow.tenant_id == tenant_id,
            Workflow.department_id == department_id,
            Workflow.archived_at.is_(None),
        )
        .order_by(Workflow.created_at.desc())
    ).all()

    workflows: list[DepartmentWorkflowItem] = []
    pub_count = 0
    paused_count = 0
    draft_count = 0
    wf_name_by_version: dict[uuid.UUID, str] = {}

    for wf in wf_rows:
        versions = db.scalars(
            select(WorkflowVersion)
            .where(
                WorkflowVersion.tenant_id == tenant_id,
                WorkflowVersion.workflow_id == wf.id,
            )
            .order_by(WorkflowVersion.version_number.desc())
        ).all()
        for v in versions:
            wf_name_by_version[v.id] = wf.name

        latest_v = versions[0] if versions else None
        pub_v = next((v for v in versions if v.status == WorkflowVersionStatus.PUBLISHED), None)
        paused_v = next((v for v in versions if v.status == WorkflowVersionStatus.PAUSED), None)

        effective_status = (
            "published"
            if pub_v
            else ("paused" if paused_v else (latest_v.status.value if latest_v else "draft"))
        )
        if effective_status == "published":
            pub_count += 1
        elif effective_status == "paused":
            paused_count += 1
        else:
            draft_count += 1

        paused_or_pub_v = paused_v or pub_v or latest_v
        workflows.append(
            DepartmentWorkflowItem(
                id=wf.id,
                name=wf.name,
                description=wf.description,
                department_id=wf.department_id,
                published_version_id=pub_v.id if pub_v else None,
                latest_version_number=latest_v.version_number if latest_v else 1,
                latest_version_status=effective_status,
                pause_reason=paused_or_pub_v.pause_reason if (paused_or_pub_v and effective_status == "paused") else None,
                updated_at=wf.updated_at,
                archived_at=wf.archived_at,
            )
        )

    run_rows = db.scalars(
        select(Run)
        .where(
            Run.tenant_id == tenant_id,
            Run.department_id == department_id,
        )
        .order_by(Run.created_at.desc())
        .limit(20)
    ).all()

    recent_runs = [
        DepartmentRunItem(
            id=r.id,
            workflow_version_id=r.workflow_version_id,
            workflow_name=wf_name_by_version.get(r.workflow_version_id, "Department Workflow"),
            trigger_source=r.trigger_source.value if hasattr(r.trigger_source, "value") else str(r.trigger_source),
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            trace_id=r.trace_id,
            total_tokens=r.total_tokens,
            cost_usd=r.cost_usd,
            error=r.error,
            started_at=r.started_at,
            finished_at=r.finished_at,
        )
        for r in run_rows
    ]

    total_runs = int(
        db.scalar(
            select(func.count(Run.id)).where(
                Run.tenant_id == tenant_id,
                Run.department_id == department_id,
            )
        )
        or 0
    )
    succeeded_runs = int(
        db.scalar(
            select(func.count(Run.id)).where(
                Run.tenant_id == tenant_id,
                Run.department_id == department_id,
                Run.status.in_([RunStatus.COMPLETED, RunStatus.SUCCEEDED]),
            )
        )
        or 0
    )
    failed_runs = int(
        db.scalar(
            select(func.count(Run.id)).where(
                Run.tenant_id == tenant_id,
                Run.department_id == department_id,
                Run.status == RunStatus.FAILED,
            )
        )
        or 0
    )
    waiting_runs = int(
        db.scalar(
            select(func.count(Run.id)).where(
                Run.tenant_id == tenant_id,
                Run.department_id == department_id,
                Run.status.in_([RunStatus.WAITING_HUMAN, RunStatus.WAITING_APPROVAL, RunStatus.NEEDS_REVIEW]),
            )
        )
        or 0
    )

    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    usage_agg = db.execute(
        select(
            func.count(UsageRecord.id).label("cnt"),
            func.coalesce(func.sum(UsageRecord.prompt_tokens), 0).label("p_tok"),
            func.coalesce(func.sum(UsageRecord.completion_tokens), 0).label("c_tok"),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("t_tok"),
            func.coalesce(func.sum(UsageRecord.cost_usd), Decimal("0.000000")).label("cost"),
        ).where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.department_id == department_id,
            UsageRecord.metric == UsageMetric.AI_TOKENS,
            UsageRecord.occurred_at >= month_start,
        )
    ).one()

    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    company_cap = tenant.monthly_ai_cost_cap_usd if tenant and tenant.monthly_ai_cost_cap_usd is not None else Decimal("5.0000")

    metrics = DepartmentOperationalMetrics(
        total_members=len(members),
        total_connections=len(connections),
        healthy_connections=healthy_conns,
        total_workflows=len(workflows),
        published_workflows=pub_count,
        paused_workflows=paused_count,
        draft_workflows=draft_count,
        total_runs=total_runs,
        succeeded_runs=succeeded_runs,
        failed_runs=failed_runs,
        waiting_review_runs=waiting_runs,
        ai_requests_count=int(usage_agg.cnt or 0),
        ai_prompt_tokens=int(usage_agg.p_tok or 0),
        ai_completion_tokens=int(usage_agg.c_tok or 0),
        ai_total_tokens=int(usage_agg.t_tok or 0),
        ai_cost_usd=Decimal(str(usage_agg.cost or 0)),
        department_monthly_cap_usd=dept.monthly_ai_cost_cap_usd,
        company_monthly_cap_usd=company_cap,
    )

    return DepartmentDashboardOut(
        department=dept_out,
        members=members,
        connections=connections,
        workflows=workflows,
        recent_runs=recent_runs,
        metrics=metrics,
    )
