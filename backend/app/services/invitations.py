import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import AuthUser
from app.db.enums import Role
from app.db.models import AuditLog, Invitation, Membership, Tenant, User
from app.db.rls import set_rls_context
from app.schemas.member import InvitationInfoOut, InvitationOut
from app.schemas.tenant import TenantOut
from app.services.member import can_manage, forbidden
from app.services.tenant import MAX_TENANTS_PER_USER, ensure_user

INVITE_TTL = timedelta(days=7)

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def to_out(invitation: Invitation) -> InvitationOut:
    pending = invitation.accepted_at is None and invitation.revoked_at is None
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        expired=pending and invitation.expires_at <= datetime.now(UTC),
    )

def _audit(db: Session, tenant_id: uuid.UUID, actor_id: uuid.UUID, action: str, entity_id, details):
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            action=action,
            entity_type="invitation",
            entity_id=str(entity_id),
            details=details,
        )
    )

def create_invitation(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_role: Role,
    email: str,
    role: Role,
) -> tuple[InvitationOut, str]:
    """Create an invitation. The raw token is returned only from here; the DB stores its hash."""
    if not can_manage(actor_role, role):
        raise forbidden()

    already_member = db.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == tenant_id, User.email == email)
    )
    if already_member is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This person is already a member")

    
    now = datetime.now(UTC)
    db.execute(
        update(Invitation)
        .where(
            Invitation.tenant_id == tenant_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at <= now,
        )
        .values(revoked_at=now)
    )

    token = secrets.token_urlsafe(32)
    try:
        with db.begin_nested():
            invitation = Invitation(
                tenant_id=tenant_id,
                email=email,
                role=role,
                token_hash=hash_token(token),
                invited_by=actor_id,
                expires_at=now + INVITE_TTL,
            )
            db.add(invitation)
            db.flush()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) == "uq_invitations_pending_email":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "This email already has a pending invitation"
            ) from None
        raise

    _audit(
        db, tenant_id, actor_id, "invitation.created", invitation.id,
        {"email": email, "role": role.value},
    )
    db.flush()
    return to_out(invitation), token

def list_pending(db: Session, tenant_id: uuid.UUID) -> list[InvitationOut]:
    rows = db.scalars(
        select(Invitation)
        .where(
            Invitation.tenant_id == tenant_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .order_by(Invitation.created_at.desc(), Invitation.id)
    )
    return [to_out(row) for row in rows]

def revoke_invitation(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_role: Role,
    invitation_id: uuid.UUID,
) -> None:
    invitation = db.scalar(
        select(Invitation)
        .where(Invitation.id == invitation_id, Invitation.tenant_id == tenant_id)
        .with_for_update()
    )
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if not can_manage(actor_role, invitation.role):
        raise forbidden()
    if invitation.accepted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Invitation was already accepted")
    if invitation.revoked_at is not None:
        return              

    invitation.revoked_at = datetime.now(UTC)
    _audit(
        db, tenant_id, actor_id, "invitation.revoked", invitation.id,
        {"email": invitation.email, "role": invitation.role.value},
    )
    db.flush()

def get_invitation_info(db: Session, token: str) -> InvitationInfoOut:
    found = db.execute(
        text(
            "select id, tenant_id, email, role, expires_at, accepted_at, revoked_at "
            "from app_find_invitation(:h)"
        ),
        {"h": hash_token(token)},
    ).one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found or link is invalid")

    now = datetime.now(UTC)
    is_expired = found.expires_at <= now
    is_valid = not found.revoked_at and not found.accepted_at and not is_expired

    tenant = db.get(Tenant, found.tenant_id)
    tenant_name = tenant.name if tenant else "Unknown Company"

    return InvitationInfoOut(
        id=found.id,
        email=found.email,
        role=Role(found.role),
        tenant_name=tenant_name,
        expires_at=found.expires_at,
        expired=is_expired,
        is_valid=is_valid,
    )

def accept_invitation(db: Session, user: AuthUser, token: str) -> TenantOut:
    """Accept an invitation as the logged-in invitee; the JWT email must match the invited email."""
    found = db.execute(
        text(
            "select id, tenant_id, email, role, expires_at, accepted_at, revoked_at "
            "from app_find_invitation(:h)"
        ),
        {"h": hash_token(token)},
    ).one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")

    user_email = (user.email or "").strip().lower()
    invited_email = (found.email or "").strip().lower()
    if not user_email or user_email != invited_email:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This invitation was sent to {found.email}, but you are signed in as {user.email or 'an unknown account'}. Please switch accounts to accept.",
        )
    gone = HTTPException(status.HTTP_410_GONE, "This invitation is no longer valid")
    if found.revoked_at or found.accepted_at or found.expires_at <= datetime.now(UTC):
        raise gone

    row = ensure_user(db, user)
    if not row.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is inactive")

    set_rls_context(db, tenant_id=found.tenant_id)
    invitation = db.scalar(
        select(Invitation).where(Invitation.id == found.id).with_for_update()
    )
    if invitation is None or invitation.accepted_at or invitation.revoked_at:
        raise gone

    already_member = db.scalar(
        select(Membership.id).where(
            Membership.tenant_id == found.tenant_id, Membership.user_id == user.id
        )
    )
    if already_member is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are already a member of this company")

    memberships = db.scalar(
        select(func.count()).select_from(Membership).where(Membership.user_id == user.id)
    )
    if memberships >= MAX_TENANTS_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Company limit reached (max {MAX_TENANTS_PER_USER} per user)",
        )

    db.add(Membership(tenant_id=found.tenant_id, user_id=user.id, role=invitation.role))
    invitation.accepted_at = datetime.now(UTC)
    invitation.accepted_by = user.id
    _audit(
        db, found.tenant_id, user.id, "invitation.accepted", invitation.id,
        {"email": invitation.email, "role": invitation.role.value},
    )
    db.flush()

    tenant = db.get(Tenant, found.tenant_id)
    return TenantOut.from_tenant(tenant, invitation.role)
