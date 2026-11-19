"""Phase 5 tests: 12 workflow templates seed idempotency, WorkflowDefinition validation, use-template, RBAC, and connector readiness."""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.api.v1.department_workflows import (
    list_department_workflow_templates,
    use_department_workflow_template,
)
from app.core.config import get_settings
from app.db.enums import ConnectionStatus, DepartmentRole, DepartmentType, Region, Role
from app.db.models import Connection, Membership, Tenant, User, WorkflowTemplate
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.schemas.department import DepartmentCreate
from app.services.department import assign_members_to_department, create_department
from app.workflows.definition import WorkflowDefinition
from app.workflows.template_seed import SEED_TEMPLATES, seed_workflow_templates


@pytest.fixture()
def db_session():
    engine = get_engine()
    conn = engine.connect()
    outer_tx = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer_tx.rollback()
        conn.close()


def test_all_6_seed_definitions_valid_and_idempotent():
    assert len(SEED_TEMPLATES) == 6
    for item in SEED_TEMPLATES:
        WorkflowDefinition.model_validate(item["definition"])

    engine = create_engine(get_settings().database_url)
    try:
        with Session(engine) as admin_db, admin_db.begin():
            n1 = seed_workflow_templates(admin_db)
            n2 = seed_workflow_templates(admin_db)
            assert n1["created"] + n1["updated"] == 6
            assert n2["updated"] == 6 and n2["created"] == 0

            keys = [item["key"] for item in SEED_TEMPLATES]
            count = int(
                admin_db.scalar(
                    select(func.count(WorkflowTemplate.id)).where(
                        WorkflowTemplate.key.in_(keys),
                        WorkflowTemplate.is_active.is_(True),
                    )
                )
                or 0
            )
            assert count == 6
    finally:
        engine.dispose()


def test_list_templates_readiness_use_template_rbac_and_isolation(db_session: Session):

    owner = User(id=uuid.uuid4(), email=f"owner-{uuid.uuid4().hex[:6]}@acme.com")
    builder = User(id=uuid.uuid4(), email=f"builder-{uuid.uuid4().hex[:6]}@acme.com")
    viewer = User(id=uuid.uuid4(), email=f"viewer-{uuid.uuid4().hex[:6]}@acme.com")
    other_owner = User(id=uuid.uuid4(), email=f"other-{uuid.uuid4().hex[:6]}@other.com")
    for u in (owner, builder, viewer, other_owner):
        set_rls_context(db_session, user_id=u.id)
        db_session.add(u)
        db_session.flush()

    t1 = Tenant(id=uuid.uuid4(), name=f"Acme-{uuid.uuid4().hex[:6]}", slug=f"acme-{uuid.uuid4().hex[:6]}", region=Region.US)
    set_rls_context(db_session, user_id=owner.id, tenant_id=t1.id)
    db_session.add(t1)
    db_session.flush()
    db_session.add_all([
        Membership(tenant_id=t1.id, user_id=owner.id, role=Role.OWNER),
        Membership(tenant_id=t1.id, user_id=builder.id, role=Role.BUILDER),
        Membership(tenant_id=t1.id, user_id=viewer.id, role=Role.VIEWER),
    ])
    db_session.flush()

    t2 = Tenant(id=uuid.uuid4(), name=f"Other-{uuid.uuid4().hex[:6]}", slug=f"other-{uuid.uuid4().hex[:6]}", region=Region.US)
    set_rls_context(db_session, user_id=other_owner.id, tenant_id=t2.id)
    db_session.add(t2)
    db_session.flush()
    db_session.add(Membership(tenant_id=t2.id, user_id=other_owner.id, role=Role.OWNER))
    db_session.flush()

    set_rls_context(db_session, user_id=owner.id, tenant_id=t1.id)
    support_dept = create_department(
        db_session,
        tenant_id=t1.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Support", department_type=DepartmentType.SUPPORT),
    )
    sales_dept = create_department(
        db_session,
        tenant_id=t1.id,
        actor_id=owner.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Sales", department_type=DepartmentType.SALES),
    )
    assign_members_to_department(db_session, tenant_id=t1.id, actor_id=owner.id, actor_role=Role.OWNER, department_id=support_dept.id, user_ids=[builder.id], role=DepartmentRole.BUILDER)
    assign_members_to_department(db_session, tenant_id=t1.id, actor_id=owner.id, actor_role=Role.OWNER, department_id=support_dept.id, user_ids=[viewer.id], role=DepartmentRole.VIEWER)

    # Connect gmail ONLY in sales_dept -> support_dept should still show ready=False
    db_session.add(
        Connection(
            tenant_id=t1.id,
            department_id=sales_dept.id,
            provider="gmail",
            display_name="Sales Gmail",
            external_account_id="sales@acme.com",
            status=ConnectionStatus.HEALTHY,
        )
    )
    db_session.flush()

    viewer_tc = TenantContext(user=viewer, tenant_id=t1.id, role=Role.VIEWER, db=db_session)
    support_tpls = list_department_workflow_templates(support_dept.id, viewer_tc)
    assert len(support_tpls) == 1
    assert support_tpls[0].key == "support_reply"
    assert all(t.department_type == "support" for t in support_tpls)
    assert all(t.ready is False for t in support_tpls)

    # Connect gmail + hubspot + slack in support_dept -> ready=True
    for prov in ("gmail", "hubspot", "slack"):
        db_session.add(
            Connection(
                tenant_id=t1.id,
                department_id=support_dept.id,
                provider=prov,
                display_name=f"Support {prov}",
                external_account_id=f"support-{prov}@acme.com",
                status=ConnectionStatus.HEALTHY,
            )
        )
    db_session.flush()
    support_tpls_ready = list_department_workflow_templates(support_dept.id, viewer_tc)
    assert all(t.ready is True for t in support_tpls_ready)

    # Viewer cannot use template (403)
    with pytest.raises(HTTPException) as exc_v:
        use_department_workflow_template(support_dept.id, "support_reply", viewer_tc)
    assert exc_v.value.status_code == 403

    # Builder creates a DRAFT workflow copy with template_key
    builder_tc = TenantContext(user=builder, tenant_id=t1.id, role=Role.BUILDER, db=db_session)
    wf = use_department_workflow_template(support_dept.id, "support_reply", builder_tc)
    assert wf.status == "draft"
    assert wf.template_key == "support_reply"
    assert wf.department_id == support_dept.id

    # Tenant isolation
    set_rls_context(db_session, user_id=other_owner.id, tenant_id=t2.id)
    other_tc = TenantContext(user=other_owner, tenant_id=t2.id, role=Role.OWNER, db=db_session)
    with pytest.raises(HTTPException) as exc_iso:
        use_department_workflow_template(support_dept.id, "support_reply", other_tc)
    assert exc_iso.value.status_code == 404
