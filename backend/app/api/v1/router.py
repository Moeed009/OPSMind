from fastapi import APIRouter

from app.api.v1 import (
    auth,
    connectors,
    current_tenant,
    department_ai,
    department_workflows,
    departments,
    email_settings,
    invitations,
    knowledge,
    me,
    members,
    reviews,
    runs,
    tenants,
)

router = APIRouter()
router.include_router(tenants.router)
router.include_router(me.router)
router.include_router(current_tenant.router)
router.include_router(members.router)
router.include_router(departments.router)
router.include_router(department_workflows.router)
router.include_router(department_workflows.global_templates_router)
router.include_router(department_ai.router)
router.include_router(invitations.router)
router.include_router(auth.router)
router.include_router(connectors.router)
router.include_router(email_settings.router)
router.include_router(runs.router)
router.include_router(reviews.router)
router.include_router(knowledge.router)
