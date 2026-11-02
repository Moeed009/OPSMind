from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import TenantContext
from app.api.rbac import require_permission
from app.core.permissions import Permission
from app.db.enums import Role
from app.db.models import AuditLog, Tenant
from app.schemas.tenant import TenantOut, TenantUpdate

router = APIRouter(prefix="/tenant", tags=["tenant"])


def _out(tenant: Tenant, role: Role) -> TenantOut:
    return TenantOut(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        region=tenant.region,
        plan=tenant.plan,
        role=role,
    )


@router.get("", response_model=TenantOut)
def read_current_tenant(
    tc: Annotated[TenantContext, Depends(require_permission(Permission.TENANT_READ))],
) -> TenantOut:
    tenant = tc.db.get(Tenant, tc.tenant_id)
    return _out(tenant, tc.role)


@router.patch("", response_model=TenantOut)
def update_current_tenant(
    payload: TenantUpdate,
    tc: Annotated[TenantContext, Depends(require_permission(Permission.TENANT_MANAGE))],
) -> TenantOut:
    tenant = tc.db.get(Tenant, tc.tenant_id)
    if tenant.name != payload.name:
        old_name = tenant.name
        tenant.name = payload.name
        tc.db.add(
            AuditLog(
                tenant_id=tc.tenant_id,
                actor_user_id=tc.user.id,
                action="tenant.updated",
                entity_type="tenant",
                entity_id=str(tc.tenant_id),
                details={"name": {"old": old_name, "new": payload.name}},
            )
        )
        tc.db.flush()
    return _out(tenant, tc.role)