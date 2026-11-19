
from __future__ import annotations

import logging
import uuid
from typing import Any

from celery import shared_task
from sqlalchemy import select

from app.db.enums import RunStatus, TriggerSource, WorkflowVersionStatus
from app.db.models import Run, Workflow, WorkflowVersion
from app.db.rls import set_rls_context
from app.db.session import get_session_factory
from app.workflows.dept_executor import (
    _run_coroutine_sync,
    execute_department_run,
    process_pending_department_events,
)

logger = logging.getLogger("opsmind.tasks")

@shared_task(
    bind=True,
    name="opsmind.execute_workflow_run",
    max_retries=3,
    default_retry_delay=5,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def execute_workflow_run_task(
    self,
    tenant_id_str: str,
    workflow_id_str: str,
    input_data: dict[str, Any],
    event_id_str: str | None = None,
) -> dict[str, Any]:
    """Background Celery task that executes a workflow DAG run with automated retries."""
    tenant_id = uuid.UUID(tenant_id_str)
    workflow_id = uuid.UUID(workflow_id_str)
    event_id = uuid.UUID(event_id_str) if event_id_str else None

    logger.info("Executing workflow run via Celery worker: wf=%s tenant=%s", workflow_id, tenant_id)
    factory = get_session_factory()

    with factory() as db, db.begin():
        set_rls_context(db, tenant_id=tenant_id)
        try:
            wf = db.scalar(
                select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.id == workflow_id)
            )
            if wf is None:
                raise ValueError(f"Workflow {workflow_id} not found")
            if wf.department_id is None:
                raise ValueError("Legacy workflows (department_id is null) are read-only and cannot be executed.")
            ver = db.scalar(
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.tenant_id == tenant_id,
                    WorkflowVersion.workflow_id == wf.id,
                    WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
                )
                .order_by(WorkflowVersion.version_number.desc())
            )
            if ver is None:
                raise ValueError(f"Workflow {workflow_id} has no active published version")
            new_run = Run(
                tenant_id=tenant_id,
                department_id=wf.department_id,
                workflow_id=wf.id,
                workflow_version_id=ver.id,
                event_id=event_id,
                trigger_source=TriggerSource.EVENT if event_id else TriggerSource.MANUAL,
                status=RunStatus.QUEUED,
                trace_id=uuid.uuid4().hex,
                input=input_data,
            )
            db.add(new_run)
            db.flush()
            run = execute_department_run(db, new_run.id)
            return {
                "run_id": str(run.id),
                "status": run.status.value,
                "cost_usd": str(run.cost_usd),
                "tokens": run.total_tokens,
            }
        except Exception as exc:
            logger.error("Celery workflow run failed (attempt %s/%s): %s", self.request.retries + 1, self.max_retries, exc)
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc) from exc
            raise

@shared_task(
    name="opsmind.poll_connector",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def poll_connector_task(self, tenant_id_str: str, connection_id_str: str) -> dict[str, Any]:
    """Background task to poll an authenticated connector for new inbound events."""
    from app.api.v1.connectors import _build_service
    from app.connectors.service import CONNECTIONS_MANAGE, CONNECTIONS_READ, Actor

    tenant_id = uuid.UUID(str(tenant_id_str))
    connection_id = uuid.UUID(str(connection_id_str))
    actor = Actor(
        tenant_id=str(tenant_id),
        user_id=None,
        permissions=frozenset({CONNECTIONS_READ, CONNECTIONS_MANAGE}),
    )
    try:
        svc = _build_service()
        res = _run_coroutine_sync(svc.poll(actor, str(connection_id)))
        from app.connectors.base import ConnectionStatus as ConnectorConnectionStatus
        from app.services import department as department_service

        if res.status in (
            ConnectorConnectionStatus.NEEDS_REAUTH,
            ConnectorConnectionStatus.ERROR,
            ConnectorConnectionStatus.REVOKED,
        ):
            factory = get_session_factory()
            with factory() as db, db.begin():
                set_rls_context(db, tenant_id=tenant_id)
                department_service.pause_workflows_for_connection(
                    db,
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                    reason=f"connection {res.status.value.lower()}",
                )
        triggered_runs = 0
        dept_runs_created = 0
        dept_run_refs: list[tuple[str, str]] = []
        if res.inserted > 0:
            from datetime import UTC, datetime

            from app.db.enums import EventStatus
            from app.db.models.event import Event

            factory = get_session_factory()
            with factory() as db, db.begin():
                set_rls_context(db, tenant_id=tenant_id)
                dept_runs, matched_event_ids = process_pending_department_events(
                    db, tenant_id=tenant_id, connection_id=connection_id
                )
                dept_runs_created = len(dept_runs)
                triggered_runs = dept_runs_created
                dept_run_refs = [(str(r.tenant_id), str(r.id)) for r in dept_runs]

                if matched_event_ids:
                    now = datetime.now(UTC)
                    matched_events = db.scalars(
                        select(Event).where(
                            Event.tenant_id == tenant_id,
                            Event.id.in_(matched_event_ids),
                        )
                    ).all()
                    for ev in matched_events:
                        if ev.status != EventStatus.PROCESSED:
                            ev.status = EventStatus.PROCESSED
                            ev.processed_at = ev.processed_at or now
                    db.flush()

            for r_tenant_id, r_id in dept_run_refs:
                try:
                    run_department_workflow_task.delay(r_tenant_id, r_id)
                except Exception as dispatch_exc:
                    logger.error(
                        "Failed to enqueue department workflow run: tenant=%s run=%s err=%s",
                        r_tenant_id,
                        r_id,
                        type(dispatch_exc).__name__,
                    )

        logger.info(
            "Connector poll complete: tenant=%s connection=%s status=%s inserted=%d dept_runs=%d legacy_runs=%d",
            tenant_id,
            connection_id,
            res.status.value,
            res.inserted,
            dept_runs_created,
            triggered_runs,
        )
        return {
            "connection_id": str(connection_id),
            "inserted_events": res.inserted,
            "triggered_runs": triggered_runs,
            "department_runs": dept_runs_created,
        }
    except Exception as exc:
        logger.error(
            "Failed to poll connector: tenant=%s connection=%s err=%s",
            tenant_id_str,
            connection_id_str,
            type(exc).__name__,
        )
        raise self.retry(exc=exc) from exc


@shared_task(name="opsmind.poll_all_connectors")
def poll_all_connectors_task() -> dict[str, Any]:
    """Periodic beat task that enqueues polling for all healthy tenant connections."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.db.enums import ConnectionStatus
    from app.db.models.connection import Connection

    settings = get_settings()
    engine = create_engine(settings.database_url)
    dispatched = 0
    try:
        with Session(engine) as db:
            conns = db.scalars(
                select(Connection).where(
                    Connection.status == ConnectionStatus.HEALTHY,
                    Connection.provider != "generic",
                )
            ).all()
            for c in conns:
                poll_connector_task.delay(str(c.tenant_id), str(c.id))
                dispatched += 1
    finally:
        engine.dispose()
    return {"dispatched_polls": dispatched}


@shared_task(
    bind=True,
    name="opsmind.run_department_workflow",
    max_retries=3,
    default_retry_delay=10,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def run_department_workflow_task(self, tenant_id_str: str, run_id_str: str) -> dict[str, Any]:
    """Execute a queued department workflow Run by ID."""
    from app.ai.exceptions import AIRateLimitError, AITimeoutError
    from app.workflows.dept_executor import execute_department_run

    tenant_id = uuid.UUID(tenant_id_str)
    run_id = uuid.UUID(run_id_str)
    factory = get_session_factory()
    with factory() as db, db.begin():
        set_rls_context(db, tenant_id=tenant_id)
        try:
            run = execute_department_run(db, run_id)
            return {"run_id": str(run.id), "status": run.status.value}
        except (AIRateLimitError, AITimeoutError, ConnectionError, TimeoutError) as exc:
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc) from exc
            raise


@shared_task(name="opsmind.schedule_department_workflows")
def schedule_department_workflows_task() -> dict[str, Any]:
    """Every-minute Celery Beat task: expire timed-out approvals and enqueue due scheduled runs."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.workflows.dept_executor import expire_pending_approvals
    from app.workflows.scheduler import enqueue_due_scheduled_runs

    settings = get_settings()
    engine = create_engine(settings.database_url)
    enqueued_count = 0
    expired_count = 0
    try:
        with Session(engine) as db, db.begin():
            expired_count = expire_pending_approvals(db)
            runs = enqueue_due_scheduled_runs(db)
            enqueued_count = len(runs)
            for r in runs:
                run_department_workflow_task.delay(str(r.tenant_id), str(r.id))
    finally:
        engine.dispose()
    return {"enqueued_runs": enqueued_count, "expired_approvals": expired_count}


@shared_task(name="opsmind.sync_connector_knowledge")
def sync_connector_knowledge_task(tenant_id_str: str, connection_id_str: str) -> dict[str, Any]:
    """Sync CRM/Invoicing/Drive records from a connection into the Knowledge Hub."""
    from app.services.knowledge_sync import sync_connector_to_knowledge_hub

    tenant_id = uuid.UUID(str(tenant_id_str))
    connection_id = uuid.UUID(str(connection_id_str))
    factory = get_session_factory()
    with factory() as db, db.begin():
        set_rls_context(db, tenant_id=tenant_id)
        return sync_connector_to_knowledge_hub(
            db, tenant_id=tenant_id, connection_id=connection_id
        )
