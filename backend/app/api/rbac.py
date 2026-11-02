from collections.abc import Callable

from fastapi import HTTPException, status

from app.api.deps import TenantContext, TenantCtx
from app.core.permissions import Permission, has_permission


def require_permission(permission: Permission) -> Callable[..., TenantContext]:
    

    def checker(tc: TenantCtx) -> TenantContext:
        if not has_permission(tc.role, permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Your role does not allow this action"
            )
        return tc

    return checker