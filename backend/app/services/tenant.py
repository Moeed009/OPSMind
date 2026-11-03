import re
import secrets
import uuid
from collections.abc import Iterator

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import AuthUser
from app.db.enums import Plan, Role, SubscriptionStatus
from app.db.models import AuditLog, Membership, Subscription, Tenant, User
from app.db.rls import set_rls_context
from app.schemas.tenant import TenantCreate, TenantOut

MAX_TENANTS_PER_USER = 3
SLUG_ATTEMPTS = 5


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50].strip("-")
    return slug if len(slug) >= 3 else "company"


def _auto_slugs(name: str) -> Iterator[str]:
    base = slugify(name)
    yield base
    for _ in range(SLUG_ATTEMPTS - 1):
        yield f"{base}-{secrets.token_hex(2)}"


def _is_slug_conflict(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == "uq_tenants_slug"


def ensure_user(db: Session, user: AuthUser) -> User:
    
    if user.email:
        db.execute(
            pg_insert(User).values(id=user.id, email=user.email).on_conflict_do_nothing()
        )
    row = db.scalar(select(User).where(User.id == user.id).with_for_update())
    if row is None:
        if not user.email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Token has no email address")
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This email is already registered to another account"
        )
    return row


def _insert_tenant(db: Session, tenant_id: uuid.UUID, payload: TenantCreate) -> Tenant:
    slugs = [payload.slug] if payload.slug else _auto_slugs(payload.name)
    for slug in slugs:
        try:
            with db.begin_nested():  
                tenant = Tenant(
                    id=tenant_id,
                    name=payload.name,
                    slug=slug,
                    region=payload.region,
                    plan=Plan.FREE,
                )
                db.add(tenant)
                db.flush()
            return tenant
        except IntegrityError as exc:
            if not _is_slug_conflict(exc):
                raise
    raise HTTPException(status.HTTP_409_CONFLICT, "This slug is already taken")


def create_tenant(db: Session, user: AuthUser, payload: TenantCreate) -> TenantOut:
    
    row = ensure_user(db, user)
    if not row.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is inactive")

    membership_count = db.scalar(
        select(func.count()).select_from(Membership).where(Membership.user_id == user.id)
    )
    if membership_count >= MAX_TENANTS_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Company limit reached (max {MAX_TENANTS_PER_USER} per user)",
        )

    tenant_id = uuid.uuid4()
    set_rls_context(db, tenant_id=tenant_id)
    tenant = _insert_tenant(db, tenant_id, payload)

    db.add(Membership(tenant_id=tenant_id, user_id=user.id, role=Role.OWNER))
    db.add(Subscription(tenant_id=tenant_id, plan=Plan.FREE, status=SubscriptionStatus.ACTIVE))
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=user.id,
            action="tenant.created",
            entity_type="tenant",
            entity_id=str(tenant_id),
            details={"slug": tenant.slug, "region": payload.region.value},
        )
    )
    db.flush()

    return TenantOut(
        id=tenant_id,
        name=tenant.name,
        slug=tenant.slug,
        region=payload.region,
        plan=Plan.FREE,
        role=Role.OWNER,
    )