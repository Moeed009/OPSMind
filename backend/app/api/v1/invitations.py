import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentUser, TenantContext, UserDb
from app.api.rbac import require_permission
from app.core.permissions import Permission
from app.db.enums import Role
from app.schemas.member import (
    InvitationAccept,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
)
from app.schemas.tenant import TenantOut
from app.services import invitations as invitation_service

router = APIRouter(prefix="/invitations", tags=["invitations"])

ManageMembers = Annotated[
    TenantContext, Depends(require_permission(Permission.MEMBERS_MANAGE))
]


@router.post("", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
def create_invitation(payload: InvitationCreate, tc: ManageMembers) -> InvitationCreated:
    out, token = invitation_service.create_invitation(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_id=tc.user.id,
        actor_role=tc.role,
        email=payload.email,
        role=Role(payload.role),
    )
    return InvitationCreated(**out.model_dump(), token=token)


@router.get("", response_model=list[InvitationOut])
def list_invitations(tc: ManageMembers) -> list[InvitationOut]:
    return invitation_service.list_pending(tc.db, tc.tenant_id)


@router.post("/accept", response_model=TenantOut)
def accept_invitation(payload: InvitationAccept, user: CurrentUser, db: UserDb) -> TenantOut:
    return invitation_service.accept_invitation(db, user, payload.token)


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(invitation_id: uuid.UUID, tc: ManageMembers) -> Response:
    invitation_service.revoke_invitation(
        tc.db,
        tenant_id=tc.tenant_id,
        actor_id=tc.user.id,
        actor_role=tc.role,
        invitation_id=invitation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)