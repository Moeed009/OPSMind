from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, UserDb
from app.db.models import Membership, Tenant, User
from app.schemas.tenant import MeOut, TenantOut

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeOut)
def read_me(user: CurrentUser, db: UserDb) -> MeOut:
    
    row = db.scalar(select(User).where(User.id == user.id))
    if row is not None and not row.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is inactive")

    rows = db.execute(
        select(
            Tenant.id,
            Tenant.name,
            Tenant.slug,
            Tenant.region,
            Tenant.plan,
            Membership.role,
        )
        .join(Membership, Membership.tenant_id == Tenant.id)
        .where(Membership.user_id == user.id)
        .order_by(Tenant.created_at, Tenant.id)
    ).all()

    return MeOut(
        id=user.id,
        email=row.email if row else user.email,
        full_name=row.full_name if row else None,
        tenants=[TenantOut(**r._mapping) for r in rows],
    )
