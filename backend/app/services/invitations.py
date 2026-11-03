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
from app.schemas.member import InvitationOut
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
    """Invite banata hai. Asli token sirf yahan se wapas jata hai, DB mein sirf hash."""
    if not can_manage(actor_role, role):
        raise forbidden()

    already_member = db.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == tenant_id, User.email == email)
    )
    if already_member is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This person is already a member")

    # Expire ho chuki pending invite naye invite ka raasta na roke.
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
        return  # idempotent

    invitation.revoked_at = datetime.now(UTC)
    _audit(
        db, tenant_id, actor_id, "invitation.revoked", invitation.id,
        {"email": invitation.email, "role": invitation.role.value},
    )
    db.flush()


def accept_invitation(db: Session, user: AuthUser, token: str) -> TenantOut:
    """Invitee (login ke baad) token deta hai. JWT email invite ke email se match hona chahiye."""
    found = db.execute(
        text(
            "select id, tenant_id, email, role, expires_at, accepted_at, revoked_at "
            "from app_find_invitation(:h)"
        ),
        {"h": hash_token(token)},
    ).one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if user.email is None or user.email != found.email:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This invitation was sent to a different email address"
        )
    gone = HTTPException(status.HTTP_410_GONE, "This invitation is no longer valid")
    if found.revoked_at or found.accepted_at or found.expires_at <= datetime.now(UTC):
        raise gone

    row = ensure_user(db, user)
    if not row.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is inactive")

    # Ab tenant context: token ka maalik hi ye tenant chun sakta hai.
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
    return TenantOut(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        region=tenant.region,
        plan=tenant.plan,
        role=invitation.role,
    )