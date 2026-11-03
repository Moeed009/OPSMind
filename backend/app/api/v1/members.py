import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import TenantContext, TenantCtx
from app.api.rbac import require_permission
from app.core.permissions import Permission
from app.schemas.member import MemberOut, MemberRoleUpdate
from app.services import member as member_service

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=list[MemberOut])
def list_company_members(
    tc: Annotated[TenantContext, Depends(require_permission(Permission.MEMBERS_READ))],
) -> list[MemberOut]:
    return member_service.list_members(tc.db, tc.tenant_id)


@router.patch("/{user_id}", response_model=MemberOut)
def change_member_role(
    user_id: uuid.UUID,
    payload: MemberRoleUpdate,
    tc: Annotated[TenantContext, Depends(require_permission(Permission.MEMBERS_MANAGE))],
) -> MemberOut:
    return member_service.change_role(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_id=tc.user.id,
        actor_role=tc.role,
        target_id=user_id,
        new_role=payload.role,
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_company_member(user_id: uuid.UUID, tc: TenantCtx) -> Response:
    """Member hatana (Owner/Admin), ya khud chhodna (koi bhi member, user_id = apni id)."""
    member_service.remove_member(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_id=tc.user.id,
        actor_role=tc.role,
        target_id=user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)