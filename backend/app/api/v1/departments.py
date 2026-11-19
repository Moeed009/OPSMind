"""Department Management, Membership, Lifecycle, and Operational Dashboard API endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import TenantCtx
from app.db.enums import DepartmentStatus
from app.schemas.department import (
    DepartmentCreate,
    DepartmentDashboardOut,
    DepartmentMemberAssignRequest,
    DepartmentMemberOut,
    DepartmentMemberRoleUpdate,
    DepartmentOut,
    DepartmentTemplateOut,
    DepartmentUpdate,
)
from app.services import department as department_service

router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("/templates", response_model=list[DepartmentTemplateOut])
def get_department_templates(tc: TenantCtx) -> list[DepartmentTemplateOut]:
    del tc
    return department_service.list_templates()


@router.get("", response_model=list[DepartmentOut])
def list_company_departments(
    tc: TenantCtx,
    include_archived: bool = Query(False),
) -> list[DepartmentOut]:
    return department_service.list_departments(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        role=tc.role,
        include_archived=include_archived,
    )


@router.post("", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_company_department(
    payload: DepartmentCreate,
    tc: TenantCtx,
) -> DepartmentOut:
    return department_service.create_department(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_id=tc.user.id,
        actor_role=tc.role,
        payload=payload,
    )


@router.get("/{department_id}", response_model=DepartmentOut)
def get_company_department(
    department_id: uuid.UUID,
    tc: TenantCtx,
) -> DepartmentOut:
    dept = department_service.check_department_access(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        role=tc.role,
        department_id=department_id,
        require_write=False,
    )
    return department_service._enrich_department(
        tc.db, tc.tenant_id, dept, user_id=tc.user.id, tenant_role=tc.role
    )


@router.get("/{department_id}/dashboard", response_model=DepartmentDashboardOut)
def get_company_department_dashboard(
    department_id: uuid.UUID,
    tc: TenantCtx,
) -> DepartmentDashboardOut:
    return department_service.get_department_dashboard(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        role=tc.role,
        department_id=department_id,
    )


@router.patch("/{department_id}", response_model=DepartmentOut)
def update_company_department(
    department_id: uuid.UUID,
    payload: DepartmentUpdate,
    tc: TenantCtx,
) -> DepartmentOut:
    return department_service.update_department(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_role=tc.role,
        department_id=department_id,
        payload=payload,
    )


@router.post("/{department_id}/activate", response_model=DepartmentOut)
def activate_company_department(
    department_id: uuid.UUID,
    tc: TenantCtx,
) -> DepartmentOut:
    return department_service.set_department_status(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_role=tc.role,
        department_id=department_id,
        new_status=DepartmentStatus.ACTIVE,
    )


@router.post("/{department_id}/deactivate", response_model=DepartmentOut)
def deactivate_company_department(
    department_id: uuid.UUID,
    tc: TenantCtx,
) -> DepartmentOut:
    return department_service.set_department_status(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_role=tc.role,
        department_id=department_id,
        new_status=DepartmentStatus.INACTIVE,
    )


@router.post("/{department_id}/archive", response_model=DepartmentOut)
def archive_company_department(
    department_id: uuid.UUID,
    tc: TenantCtx,
) -> DepartmentOut:
    return department_service.set_department_status(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_role=tc.role,
        department_id=department_id,
        new_status=DepartmentStatus.ARCHIVED,
    )


@router.get("/{department_id}/members", response_model=list[DepartmentMemberOut])
def get_department_members(
    department_id: uuid.UUID,
    tc: TenantCtx,
) -> list[DepartmentMemberOut]:
    return department_service.list_department_members(
        tc.db,
        tenant_id=tc.tenant_id,
        user_id=tc.user.id,
        role=tc.role,
        department_id=department_id,
    )


@router.post("/{department_id}/members", response_model=list[DepartmentMemberOut], status_code=status.HTTP_201_CREATED)
def assign_department_members(
    department_id: uuid.UUID,
    payload: DepartmentMemberAssignRequest,
    tc: TenantCtx,
) -> list[DepartmentMemberOut]:
    return department_service.assign_members_to_department(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_id=tc.user.id,
        actor_role=tc.role,
        department_id=department_id,
        user_ids=payload.user_ids,
        role=payload.role,
    )


@router.patch("/{department_id}/members/{user_id}", response_model=DepartmentMemberOut)
def update_department_member_role(
    department_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: DepartmentMemberRoleUpdate,
    tc: TenantCtx,
) -> DepartmentMemberOut:
    return department_service.update_department_member_role(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_id=tc.user.id,
        actor_role=tc.role,
        department_id=department_id,
        target_user_id=user_id,
        new_role=payload.role,
    )


@router.delete("/{department_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_department_member(
    department_id: uuid.UUID,
    user_id: uuid.UUID,
    tc: TenantCtx,
) -> Response:
    department_service.remove_member_from_department(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_role=tc.role,
        department_id=department_id,
        target_user_id=user_id,
        actor_id=tc.user.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
