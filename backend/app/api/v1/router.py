from fastapi import APIRouter

from app.api.v1 import current_tenant, me, tenants

router = APIRouter()
router.include_router(tenants.router)
router.include_router(me.router)
router.include_router(current_tenant.router)