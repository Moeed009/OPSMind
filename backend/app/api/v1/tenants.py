from fastapi import APIRouter, status

from app.api.deps import CurrentUser, UserDb
from app.schemas.tenant import TenantCreate, TenantOut
from app.services.tenant import create_tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_company(payload: TenantCreate, user: CurrentUser, db: UserDb) -> TenantOut:
    
    return create_tenant(db, user, payload)