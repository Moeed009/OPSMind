import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import Role
from app.db.models import AuditLog, Membership, User
from app.schemas.member import MemberOut

ADMIN_MANAGEABLE = frozenset({Role.BUILDER, Role.REVIEWER, Role.VIEWER})
LAST_OWNER_MESSAGE = "A company must keep at least one Owner"


def can_manage(actor_role: Role, target_role: Role, new_role: Role | None = None) -> bool:
    if actor_role == Role.OWNER:
        return True
    if actor_role == Role.ADMIN:
        if target_role not in ADMIN_MANAGEABLE:
            return False
        return new_role is None or new_role in ADMIN_MANAGEABLE
    return False


def forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Your role does not allow this action")


def _lock_owner_ids(db: Session, tenant_id: uuid.UUID) -> list[uuid.UUID]:
    """Owners ki rows lock: do saath-saath requests aakhri Owner nahi hata sakti."""
    return list(
        db.scalars(
            select(Membership.user_id)
            .where(Membership.tenant_id == tenant_id, Membership.role == Role.OWNER)
            .order_by(Membership.id)
            .with_for_update()
        )
    )


def _get_membership(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    membership = db.scalar(
        select(Membership)
        .where(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
        .with_for_update()
    )
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return membership


def _member_query(tenant_id: uuid.UUID):
    return (
        select(
            Membership.user_id,
            User.email,
            User.full_name,
            Membership.role,
            Membership.created_at.label("joined_at"),
        )
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == tenant_id)
    )


def list_members(db: Session, tenant_id: uuid.UUID) -> list[MemberOut]:
    rows = db.execute(_member_query(tenant_id).order_by(Membership.created_at, User.email))
    return [MemberOut(**row._mapping) for row in rows]


def _load_member(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> MemberOut:
    row = db.execute(_member_query(tenant_id).where(Membership.user_id == user_id)).one()
    return MemberOut(**row._mapping)


def change_role(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_role: Role,
    target_id: uuid.UUID,
    new_role: Role,
) -> MemberOut:
    owners = _lock_owner_ids(db, tenant_id)
    membership = _get_membership(db, tenant_id, target_id)
    if not can_manage(actor_role, membership.role, new_role):
        raise forbidden()
    if membership.role == new_role:
        return _load_member(db, tenant_id, target_id)
    if membership.role == Role.OWNER and len(owners) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, LAST_OWNER_MESSAGE)

    old_role = membership.role
    membership.role = new_role
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            action="member.role_changed",
            entity_type="membership",
            entity_id=str(target_id),
            details={"user_id": str(target_id), "old": old_role.value, "new": new_role.value},
        )
    )
    db.flush()
    return _load_member(db, tenant_id, target_id)


def remove_member(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_role: Role,
    target_id: uuid.UUID,
) -> None:
    owners = _lock_owner_ids(db, tenant_id)
    membership = _get_membership(db, tenant_id, target_id)
    is_self = target_id == actor_id
    if not is_self and not can_manage(actor_role, membership.role):
        raise forbidden()
    if membership.role == Role.OWNER and len(owners) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, LAST_OWNER_MESSAGE)

    role = membership.role
    db.delete(membership)
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            action="member.left" if is_self else "member.removed",
            entity_type="membership",
            entity_id=str(target_id),
            details={"user_id": str(target_id), "role": role.value},
        )
    )
    db.flush()