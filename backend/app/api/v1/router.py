from fastapi import APIRouter

from app.api.v1 import auth, current_tenant, invitations, me, members, tenants

router = APIRouter()
router.include_router(tenants.router)
router.include_router(me.router)
router.include_router(current_tenant.router)
router.include_router(members.router)
router.include_router(invitations.router)
router.include_router(auth.router)
