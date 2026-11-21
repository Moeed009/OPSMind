from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.exceptions import AIError
from app.ai.redaction import redact_preview
from app.ai.services.dept_ai_service import AiContext, DepartmentAiService
from app.db.enums import (
    ConnectionStatus,
    DepartmentStatus,
    EventStatus,
    Role,
    RunStatus,
    RunStepStatus,
    TriggerSource,
    WorkflowApprovalStatus,
    WorkflowVersionStatus,
)
from app.db.models import Connection, Department, Event, Run, RunStep, Workflow, WorkflowApproval, WorkflowVersion
from app.services.department_workflows import _require_dept_role
from app.workflows.action_registry import execute_registered_action
from app.workflows.definition import WorkflowDefinition, WorkflowNode
from app.workflows.expression import evaluate_expression
from app.workflows.templating import render_params


def _run_async(coro):
    import sys

    def _runner(c):
        if sys.platform == "win32":
            return asyncio.run(c, loop_factory=asyncio.SelectorEventLoop)
        return asyncio.run(c)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _runner(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_runner, coro).result()


_run_coroutine_sync = _run_async


def _redact_dict(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return {str(k): redact_preview(v, max_chars=250) for k, v in list(data.items())[:20]}
    return {"preview": redact_preview(data, max_chars=250)}


def _build_adj(defn: WorkflowDefinition) -> tuple[dict[str, WorkflowNode], dict[str, list[tuple[str, str | None]]], list[str]]:
    nodes_by_id = {n.id: n for n in defn.nodes}
    in_degree = {n.id: 0 for n in defn.nodes}
    adj: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for edge in defn.edges:
        adj[edge.source].append((edge.target, edge.label))
        in_degree[edge.target] += 1
    roots = [n.id for n in defn.nodes if in_degree[n.id] == 0]
    return nodes_by_id, adj, roots


def _walk_from(
    db: Session,
    *,
    run: Run,
    defn: WorkflowDefinition,
    context: dict[str, Any],
    step_index_start: int,
    completed: dict[str, str | None] | None = None,
    ai_service: DepartmentAiService | None = None,
) -> Run:
    """Execute the DAG with join semantics: a node runs once every incoming edge is resolved
    and at least one of them is active. Untaken condition branches propagate as dead paths.
    `completed` maps already-finished node ids to their chosen branch (None = all edges)."""
    nodes_by_id, adj, roots = _build_adj(defn)
    svc = ai_service or DepartmentAiService()
    done = dict(completed or {})
    pending = {nid: 0 for nid in nodes_by_id}
    for edges in adj.values():
        for target, _ in edges:
            pending[target] += 1
    has_active_parent: set[str] = set()
    queue: list[str] = list(roots)
    step_idx = step_index_start

    def resolve(nid: str, chosen: str | None) -> None:
        for target, lbl in adj.get(nid, []):
            if chosen is None or lbl == chosen:
                has_active_parent.add(target)
            pending[target] -= 1
            if pending[target] == 0:
                if target in has_active_parent:
                    queue.append(target)
                else:
                    resolve(target, "__dead__")

    while queue:
        nid = queue.pop(0)
        if nid in done:
            resolve(nid, done[nid])
            continue
        node = nodes_by_id[nid]

        step = RunStep(
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_index=step_idx,
            node_id=node.id,
            step_type=node.type,
            status=RunStepStatus.RUNNING,
            input=_redact_dict(context),
            started_at=datetime.now(UTC),
        )
        db.add(step)
        db.flush()
        step_idx += 1

        if node.type in ("classify", "extract", "agent", "ai"):
            task_name = node.task if node.type == "ai" else node.type
            ai_input = {
                "trigger": context.get("trigger") or {},
                "steps": {k: v for k, v in context.items() if k != "trigger"},
            }
            try:
                ai_res = _run_async(
                    svc.run(
                        db,
                        AiContext(
                            tenant_id=run.tenant_id,
                            department_id=run.department_id,
                            workflow_run_id=run.id,
                            node_id=node.id,
                        ),
                        task_name,
                        ai_input,
                        node,
                    )
                )
            except AIError as exc:
                step.status = RunStepStatus.FAILED
                step.error = str(exc)
                step.finished_at = datetime.now(UTC)
                run.status = RunStatus.FAILED
                run.error = f"AI step '{node.id}' failed: {exc}"
                run.finished_at = datetime.now(UTC)
                db.flush()
                return run

            ai_out = dict(ai_res.output)
            if node.type == "agent" and getattr(node, "require_sources", False):
                has_retrieved = any(
                    isinstance(v, dict) and v.get("match_count", 0) > 0
                    for k, v in context.items()
                    if k != "trigger"
                )
                if not has_retrieved or not ai_out.get("has_reliable_source", True):
                    ai_out["has_reliable_source"] = False
                    ai_out["sources"] = []
                    ai_out["text"] = (
                        "I don't know based on the available company policy documents. "
                        "Escalating to human review."
                    )
                    ai_out["confidence"] = 0.0

            step.output = _redact_dict(ai_out)
            step.model = ai_res.model
            step.latency_ms = ai_res.latency_ms
            step.prompt_tokens = ai_res.input_tokens
            step.completion_tokens = ai_res.output_tokens
            step.finished_at = datetime.now(UTC)
            context[node.id] = {**ai_out, "output": ai_out}

            if ai_res.status == "needs_review":
                step.status = RunStepStatus.COMPLETED
                db.add(
                    WorkflowApproval(
                        tenant_id=run.tenant_id,
                        department_id=run.department_id,
                        run_id=run.id,
                        node_id=node.id,
                        required_role="reviewer",
                        status=WorkflowApprovalStatus.PENDING,
                        payload={"ai_output": ai_out, "confidence": ai_res.confidence},
                    )
                )
                run.status = RunStatus.NEEDS_REVIEW
                run.output = dict(context)
                db.flush()
                return run

            step.status = RunStepStatus.COMPLETED
            db.flush()
            done[nid] = None
            resolve(nid, None)

        elif node.type == "retrieve":
            ret_out = _execute_retrieve_node(db, run=run, node=node, context=context)
            step.status = RunStepStatus.COMPLETED
            step.output = _redact_dict(ret_out)
            step.finished_at = datetime.now(UTC)
            context[node.id] = ret_out
            db.flush()
            done[nid] = None
            resolve(nid, None)

        elif node.type == "wait":
            wait_out = {"wait_seconds": node.wait_seconds, "waited": True}
            if node.until_expression:
                wait_out["until_met"] = bool(evaluate_expression(node.until_expression, context))
            step.status = RunStepStatus.COMPLETED
            step.output = wait_out
            step.finished_at = datetime.now(UTC)
            context[node.id] = wait_out
            db.flush()
            done[nid] = None
            resolve(nid, None)

        elif node.type == "condition":
            branch_val = evaluate_expression(node.expression, context)
            chosen_label = "true" if branch_val else "false"
            step.status = RunStepStatus.COMPLETED
            step.output = {"result": branch_val, "branch": chosen_label}
            step.finished_at = datetime.now(UTC)
            context[node.id] = {"result": branch_val}
            db.flush()
            done[nid] = chosen_label
            resolve(nid, chosen_label)

        elif node.type == "approval":
            expires_at = (
                datetime.now(UTC) + timedelta(hours=node.timeout_hours)
                if node.timeout_hours is not None
                else None
            )
            step.status = RunStepStatus.COMPLETED
            step.output = {"approval_requested": True, "required_role": node.role}
            step.finished_at = datetime.now(UTC)
            db.add(
                WorkflowApproval(
                    tenant_id=run.tenant_id,
                    department_id=run.department_id,
                    run_id=run.id,
                    node_id=node.id,
                    required_role=node.role,
                    status=WorkflowApprovalStatus.PENDING,
                    payload=_redact_dict(context),
                    expires_at=expires_at,
                )
            )
            run.status = RunStatus.WAITING_APPROVAL
            run.output = dict(context)
            db.flush()
            return run

        elif node.type == "action":
            rendered = render_params(node.params, context)
            trigger_ctx = context.get("trigger") if isinstance(context.get("trigger"), dict) else {}
            replay_root_raw = trigger_ctx.get("_replay_of_run_id")
            idem_run_id = uuid.UUID(str(replay_root_raw)) if replay_root_raw else run.id
            act_res = execute_registered_action(
                db,
                tenant_id=run.tenant_id,
                department_id=run.department_id,
                run_id=run.id,
                run_step_id=step.id,
                action_key=node.action,
                params=rendered,
                context=context,
                node_id=node.id,
                idempotency_run_id=idem_run_id,
            )
            step.finished_at = datetime.now(UTC)
            if not act_res.success:
                step.status = RunStepStatus.FAILED
                step.error = act_res.error
                run.status = RunStatus.FAILED
                run.error = act_res.error or f"Action '{node.action}' failed"
                run.finished_at = datetime.now(UTC)
                db.flush()
                return run

            step.status = RunStepStatus.COMPLETED
            step.output = _redact_dict(act_res.response_data or {"external_ref": act_res.external_ref})
            context[node.id] = {"external_ref": act_res.external_ref, "ok": True}
            db.flush()
            done[nid] = None
            resolve(nid, None)

    run.status = RunStatus.SUCCEEDED
    run.output = dict(context)
    run.finished_at = datetime.now(UTC)
    db.flush()
    return run


def execute_department_run(
    db: Session,
    run_id: uuid.UUID,
    *,
    ai_service: DepartmentAiService | None = None,
) -> Run:
    run = db.scalar(select(Run).where(Run.id == run_id))
    if run is None:
        raise ValueError(f"Run {run_id} not found")

    ver = db.scalar(select(WorkflowVersion).where(WorkflowVersion.id == run.workflow_version_id))
    if ver is None:
        run.status = RunStatus.FAILED
        run.error = "Workflow version not found"
        run.finished_at = datetime.now(UTC)
        db.flush()
        return run

    defn = WorkflowDefinition.model_validate(ver.definition)
    run.status = RunStatus.RUNNING
    run.started_at = run.started_at or datetime.now(UTC)
    db.flush()

    context: dict[str, Any] = {"trigger": dict(run.input or {})}
    return _walk_from(
        db,
        run=run,
        defn=defn,
        context=context,
        step_index_start=0,
        ai_service=ai_service,
    )


def decide_workflow_approval(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    department_id: uuid.UUID,
    approval_id: uuid.UUID,
    decision: str,
    comment: str | None = None,
    ai_service: DepartmentAiService | None = None,
) -> WorkflowApproval:
    approval = db.scalar(
        select(WorkflowApproval).where(
            WorkflowApproval.tenant_id == tenant_id,
            WorkflowApproval.department_id == department_id,
            WorkflowApproval.id == approval_id,
        )
    )
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")

    allowed = (
        {Role.OWNER, Role.ADMIN}
        if approval.required_role == "admin"
        else {Role.OWNER, Role.ADMIN, Role.REVIEWER}
    )
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles=allowed,
    )
    if approval.status != WorkflowApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approval has already been decided.")

    now = datetime.now(UTC)
    run = db.scalar(select(Run).where(Run.id == approval.run_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Associated run not found")

    if approval.expires_at is not None and approval.expires_at <= now:
        approval.status = WorkflowApprovalStatus.EXPIRED
        approval.decided_at = now
        run.status = RunStatus.CANCELLED
        run.finished_at = now
        db.flush()
        return approval

    approval.decided_by = user_id
    approval.decided_at = now
    approval.comment = comment

    if decision == "reject":
        approval.status = WorkflowApprovalStatus.REJECTED
        run.status = RunStatus.CANCELLED
        run.finished_at = now
        db.flush()
        return approval

    if decision != "approve":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "decision must be 'approve' or 'reject'")

    approval.status = WorkflowApprovalStatus.APPROVED
    ver = db.scalar(select(WorkflowVersion).where(WorkflowVersion.id == run.workflow_version_id))
    if ver is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow version for this run no longer exists")
    defn = WorkflowDefinition.model_validate(ver.definition)
    existing_steps = db.scalars(select(RunStep).where(RunStep.run_id == run.id)).all()
    completed: dict[str, str | None] = {}
    for st in existing_steps:
        if st.status != RunStepStatus.COMPLETED or st.node_id is None:
            continue
        branch = (st.output or {}).get("branch") if st.step_type == "condition" else None
        completed[st.node_id] = str(branch) if branch is not None else None

    run.status = RunStatus.RUNNING
    db.flush()
    _walk_from(
        db,
        run=run,
        defn=defn,
        context=dict(run.output or {"trigger": dict(run.input or {})}),
        step_index_start=len(existing_steps),
        completed=completed,
        ai_service=ai_service,
    )
    return approval


def expire_pending_approvals(db: Session, *, now_utc: datetime | None = None) -> int:
    now = now_utc or datetime.now(UTC)
    expired_rows = db.scalars(
        select(WorkflowApproval).where(
            WorkflowApproval.status == WorkflowApprovalStatus.PENDING,
            WorkflowApproval.expires_at.is_not(None),
            WorkflowApproval.expires_at <= now,
        )
    ).all()
    count = 0
    for app in expired_rows:
        app.status = WorkflowApprovalStatus.EXPIRED
        app.decided_at = now
        run = db.scalar(select(Run).where(Run.id == app.run_id))
        if run is not None and run.status in (RunStatus.WAITING_APPROVAL, RunStatus.NEEDS_REVIEW):
            run.status = RunStatus.CANCELLED
            run.finished_at = now
        count += 1
    if count:
        db.flush()
    return count


logger = logging.getLogger("opsmind.dept_executor")

_EVENT_SOURCE_ALIASES: dict[str, set[str]] = {
    "email.received": {"email.received", "gmail.new_email"},
    "gmail.new_email": {"gmail.new_email", "email.received"},
    "message.received": {"message.received", "slack.message"},
    "slack.message": {"slack.message", "message.received"},
}


def compatible_event_sources(source: str, provider: str | None = None) -> set[str]:
    """Return compatible trigger source names for an inbound connector event."""
    normalized = (source or "").strip().lower()
    if not normalized:
        return set()
    sources = set(_EVENT_SOURCE_ALIASES.get(normalized, {normalized}))
    if provider:
        prov = provider.strip().lower()
        if prov == "gmail" and normalized == "email.received":
            sources.add("gmail.new_email")
        elif prov == "slack" and normalized == "message.received":
            sources.add("slack.message")
    return sources


def dispatch_department_event_triggers(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    source: str,
    payload: dict[str, Any],
    provider: str | None = None,
    event_id: uuid.UUID | None = None,
) -> list[Run]:
    """Queue runs for active department workflows matching an event source when connector is healthy."""
    dept = db.scalar(
        select(Department).where(
            Department.tenant_id == tenant_id,
            Department.id == department_id,
        )
    )
    if dept is None or dept.status != DepartmentStatus.ACTIVE:
        return []

    resolved_provider = (provider or source.split(".", 1)[0]).strip().lower()
    if resolved_provider == "email":
        resolved_provider = "gmail"
    healthy = db.scalar(
        select(Connection.id).where(
            Connection.tenant_id == tenant_id,
            Connection.department_id == department_id,
            Connection.provider == resolved_provider,
            Connection.status == ConnectionStatus.HEALTHY,
        )
    )
    if healthy is None:
        return []

    accepted_sources = compatible_event_sources(source, provider=resolved_provider)
    if not accepted_sources:
        return []

    clean_payload = dict(payload or {})
    clean_payload.pop("tenant_id", None)
    clean_payload.pop("department_id", None)

    rows = db.execute(
        select(Workflow, WorkflowVersion)
        .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
        .where(
            Workflow.tenant_id == tenant_id,
            Workflow.department_id == department_id,
            Workflow.archived_at.is_(None),
            WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
        )
    ).all()

    created: list[Run] = []
    for wf, ver in rows:
        trig = (ver.definition or {}).get("trigger") or {}
        if trig.get("type") != "event":
            continue
        trig_source = str(trig.get("source") or "").strip().lower()
        if trig_source not in accepted_sources:
            continue

        idempotency_key: str | None = None
        if event_id is not None:
            idempotency_key = f"dept_event:{event_id}:{wf.id}"
            existing = db.scalar(
                select(Run.id).where(
                    Run.tenant_id == tenant_id,
                    Run.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                continue

        run = Run(
            tenant_id=tenant_id,
            department_id=department_id,
            workflow_id=wf.id,
            workflow_version_id=ver.id,
            event_id=event_id,
            idempotency_key=idempotency_key,
            trigger_source=TriggerSource.EVENT,
            status=RunStatus.QUEUED,
            trace_id=uuid.uuid4().hex,
            input=clean_payload,
        )
        db.add(run)
        db.flush()
        created.append(run)
    return created


def process_pending_department_events(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID | None = None,
) -> tuple[list[Run], set[uuid.UUID]]:
    """Dispatch RECEIVED connector events to matching active department workflows.

    Returns (created_runs, matched_event_ids) without overwriting EventStatus for
    unmatched events so legacy WorkflowEngine.process_pending_events can still inspect them.
    """
    evt_stmt = select(Event).where(
        Event.tenant_id == tenant_id,
        Event.status == EventStatus.RECEIVED,
    )
    if connection_id is not None:
        evt_stmt = evt_stmt.where(Event.connection_id == connection_id)
    events = db.scalars(evt_stmt.order_by(Event.created_at.asc()).limit(50)).all()
    if not events:
        return [], set()

    created_runs: list[Run] = []
    matched_event_ids: set[uuid.UUID] = set()
    for ev in events:
        conn = db.scalar(
            select(Connection).where(
                Connection.tenant_id == tenant_id,
                Connection.id == ev.connection_id,
            )
        )
        if conn is None or conn.department_id is None:
            continue

        try:
            runs = dispatch_department_event_triggers(
                db,
                tenant_id=tenant_id,
                department_id=conn.department_id,
                source=ev.event_type,
                payload=dict(ev.payload or {}),
                provider=conn.provider,
                event_id=ev.id,
            )
        except Exception as exc:
            logger.warning(
                "Department event dispatch failed: tenant=%s dept=%s event=%s err=%s",
                tenant_id,
                conn.department_id,
                ev.id,
                type(exc).__name__,
            )
            continue

        if runs:
            matched_event_ids.add(ev.id)
            created_runs.extend(runs)
            logger.info(
                "Dispatched %d department workflow run(s): tenant=%s dept=%s event=%s type=%s",
                len(runs),
                tenant_id,
                conn.department_id,
                ev.id,
                ev.event_type,
            )

    return created_runs, matched_event_ids


def _execute_retrieve_node(
    db: Session,
    *,
    run: Run,
    node: Any,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve tenant- & department-scoped Knowledge Hub chunks, or aggregate ops metrics."""
    if getattr(node, "mode", "knowledge") == "aggregate_ops":
        from sqlalchemy import func

        since = datetime.now(UTC) - timedelta(days=7)
        rows = db.execute(
            select(Department.name, Department.department_type, Run.status, func.count(Run.id))
            .join(Department, Department.id == Run.department_id)
            .where(
                Run.tenant_id == run.tenant_id,
                Run.department_id.is_not(None),
                Run.created_at >= since,
                Department.status != DepartmentStatus.ARCHIVED,
            )
            .group_by(Department.name, Department.department_type, Run.status)
        ).all()
        summary: dict[str, dict[str, Any]] = {}
        for dept_name, dept_type, st, cnt in rows:
            key = str(dept_name)
            entry = summary.setdefault(
                key, {"department_type": dept_type.value if hasattr(dept_type, "value") else str(dept_type), "total_runs": 0, "by_status": {}}
            )
            entry["total_runs"] += int(cnt)
            entry["by_status"][st.value if hasattr(st, "value") else str(st)] = int(cnt)
        return {
            "mode": "aggregate_ops",
            "window_days": 7,
            "departments": summary,
            "match_count": len(summary),
            "sources": ["internal_operations_aggregate_runs"],
        }

    from app.services.retrieval import search_knowledge_chunks

    rendered_query = render_params({"q": getattr(node, "query_template", "")}, context).get("q", "")
    query_str = str(rendered_query or "").strip()
    top_k = int(getattr(node, "top_k", 5) or 5)
    include_shared = bool(getattr(node, "include_company_shared", True))

    retrieved = search_knowledge_chunks(
        db,
        tenant_id=run.tenant_id,
        department_id=run.department_id,
        query=query_str,
        top_k=top_k,
        include_company_shared=include_shared,
    )
    matches: list[dict[str, Any]] = []
    sources: list[str] = []
    for item in retrieved:
        matches.append(item.to_dict(preview_chars=600))
        if item.title not in sources:
            sources.append(item.title)
    return {
        "mode": "knowledge",
        "query": query_str,
        "matches": matches,
        "match_count": len(matches),
        "sources": sources,
    }


def replay_department_run(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_role: Role,
    ai_service: DepartmentAiService | None = None,
) -> Run:
    """Create and execute a new Run replaying the original run's trigger input and workflow version."""
    _require_dept_role(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_role=tenant_role,
        department_id=department_id,
        allowed_roles={Role.OWNER, Role.ADMIN, Role.BUILDER},
    )
    orig = db.scalar(
        select(Run).where(
            Run.tenant_id == tenant_id,
            Run.department_id == department_id,
            Run.id == run_id,
        )
    )
    if orig is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    orig_input = dict(orig.input or {})
    root_run_id = str(orig_input.get("_replay_of_run_id") or orig.id)
    orig_input["_replay_of_run_id"] = root_run_id

    new_run = Run(
        tenant_id=tenant_id,
        department_id=department_id,
        workflow_id=orig.workflow_id,
        workflow_version_id=orig.workflow_version_id,
        event_id=orig.event_id,
        trigger_source=orig.trigger_source,
        status=RunStatus.QUEUED,
        trace_id=uuid.uuid4().hex,
        input=orig_input,
    )
    db.add(new_run)
    db.flush()
    return execute_department_run(db, new_run.id, ai_service=ai_service)


def _audit_cross_dept_rejection(
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    workflow_department_id: uuid.UUID | None,
    connection_id: uuid.UUID,
    connection_department_id: uuid.UUID | None,
    stage: str,
) -> None:
    from app.db.models.audit_log import AuditLog
    from app.db.rls import set_rls_context
    from app.db.session import get_session_factory

    try:
        factory = get_session_factory()
        with factory() as adb, adb.begin():
            set_rls_context(adb, user_id=actor_id, tenant_id=tenant_id)
            adb.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_user_id=actor_id,
                    action="workflow.cross_department_connection_rejected",
                    entity_type="connection",
                    entity_id=str(connection_id),
                    details={
                        "stage": stage,
                        "workflow_department_id": str(workflow_department_id) if workflow_department_id else None,
                        "connection_department_id": str(connection_department_id) if connection_department_id else None,
                    },
                )
            )
    except Exception:
        pass


def _validate_workflow_connections(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    workflow_department_id: uuid.UUID | None,
    trigger_connection_id: uuid.UUID | None,
    dag: Any,
    actor_id: uuid.UUID | None = None,
    stage: str = "save",
) -> None:
    """Enforces strict connection scope isolation between company-level and department-level workflows."""
    referenced_conn_ids: set[uuid.UUID] = set()
    if trigger_connection_id is not None:
        referenced_conn_ids.add(trigger_connection_id)

    for node in getattr(dag, "nodes", []) or []:
        cfg = getattr(node, "config", None) or (node.get("config") if isinstance(node, dict) else None) or {}
        raw_cid = cfg.get("connection_id")
        if raw_cid:
            try:
                referenced_conn_ids.add(uuid.UUID(str(raw_cid)))
            except ValueError as exc:
                lbl = getattr(node, "label", None) or (node.get("label") if isinstance(node, dict) else "node")
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"Node '{lbl}' has an invalid connection_id UUID.",
                ) from exc

    for cid in referenced_conn_ids:
        conn = db.scalar(select(Connection).where(Connection.tenant_id == tenant_id, Connection.id == cid))
        if conn is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Connection {cid} not found in this company.")
        if workflow_department_id is not None:
            if conn.department_id != workflow_department_id:
                _audit_cross_dept_rejection(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    workflow_department_id=workflow_department_id,
                    connection_id=cid,
                    connection_department_id=conn.department_id,
                    stage=stage,
                )
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Department workflow can only use connections assigned to the same department.",
                )
        else:
            if conn.department_id is not None:
                _audit_cross_dept_rejection(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    workflow_department_id=None,
                    connection_id=cid,
                    connection_department_id=conn.department_id,
                    stage=stage,
                )
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Company-wide workflow cannot use a department-restricted connection.",
                )
        if stage in ("publish", "resume") and conn.status != ConnectionStatus.HEALTHY:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Connection '{conn.display_name}' is {conn.status.value} and must be healthy before publishing or resuming.",
            )


class WorkflowEngine:
    """Department-scoped connection & event trigger runtime verifier."""

    @classmethod
    def start_run(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        input_data: dict[str, Any],
        trigger_source: TriggerSource = TriggerSource.MANUAL,
        event_id: uuid.UUID | None = None,
    ) -> Run:
        from decimal import Decimal

        from app.db.models.audit_log import AuditLog

        now = datetime.now(UTC)
        wf = db.get(Workflow, workflow_id)
        if not wf or wf.tenant_id != tenant_id:
            raise ValueError(f"Workflow {workflow_id} not found for this tenant.")
        if wf.department_id is None:
            raise ValueError("Legacy workflows (department_id is null) are read-only and cannot be executed.")

        dept = db.scalar(select(Department).where(Department.tenant_id == tenant_id, Department.id == wf.department_id))
        if not dept or dept.status != DepartmentStatus.ACTIVE:
            raise ValueError("Cannot execute workflow: department is inactive or archived.")

        version = db.scalar(
            select(WorkflowVersion)
            .where(
                WorkflowVersion.tenant_id == tenant_id,
                WorkflowVersion.workflow_id == workflow_id,
                WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
            )
            .order_by(WorkflowVersion.version_number.desc())
        )
        if not version:
            raise ValueError(f"No published version found for workflow {workflow_id}.")

        idem_key = f"evt:{event_id}:wf:{wf.id}" if event_id is not None else None
        if idem_key is not None:
            existing_run = db.scalar(select(Run).where(Run.tenant_id == tenant_id, Run.idempotency_key == idem_key))
            if existing_run is not None:
                return existing_run

        run = Run(
            tenant_id=tenant_id,
            department_id=wf.department_id,
            workflow_id=wf.id,
            workflow_version_id=version.id,
            event_id=event_id,
            trigger_source=trigger_source,
            status=RunStatus.RUNNING,
            trace_id=uuid.uuid4().hex[:32],
            idempotency_key=idem_key,
            input=input_data,
            cost_usd=Decimal("0.000000"),
            total_tokens=0,
            started_at=now,
        )
        db.add(run)
        db.flush()

        referenced_cids: set[uuid.UUID] = set()
        if version.trigger_connection_id is not None:
            referenced_cids.add(version.trigger_connection_id)
        defn = version.definition if isinstance(version.definition, dict) else {}
        for n in defn.get("nodes") or []:
            raw_cid = (n.get("config") or {}).get("connection_id") if isinstance(n, dict) else None
            if raw_cid:
                referenced_cids.add(uuid.UUID(str(raw_cid)))

        for cid in referenced_cids:
            conn_row = db.scalar(select(Connection).where(Connection.tenant_id == tenant_id, Connection.id == cid))
            if conn_row is None or conn_row.department_id != wf.department_id:
                db.add(
                    AuditLog(
                        tenant_id=tenant_id,
                        action="workflow.cross_department_connection_rejected",
                        entity_type="connection",
                        entity_id=str(cid),
                        details={"stage": "runtime", "run_id": str(run.id), "workflow_id": str(wf.id)},
                    )
                )
                run.status = RunStatus.FAILED
                run.error = "Cross-department or unauthorized connection reference rejected at runtime."
                run.finished_at = datetime.now(UTC)
                db.flush()
                return run

        run.status = RunStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)
        db.flush()
        return run

    @classmethod
    def process_pending_events(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        connection_id: uuid.UUID | None = None,
    ) -> int:
        evt_stmt = select(Event).where(Event.tenant_id == tenant_id, Event.status == EventStatus.RECEIVED)
        if connection_id is not None:
            evt_stmt = evt_stmt.where(Event.connection_id == connection_id)
        events = db.scalars(evt_stmt.order_by(Event.created_at.asc()).limit(50)).all()
        if not events:
            return 0

        active_dept_ids = set(
            db.scalars(
                select(Department.id).where(
                    Department.tenant_id == tenant_id,
                    Department.status == DepartmentStatus.ACTIVE,
                )
            ).all()
        )
        pub_versions = db.execute(
            select(WorkflowVersion, Workflow)
            .join(Workflow, WorkflowVersion.workflow_id == Workflow.id)
            .where(
                WorkflowVersion.tenant_id == tenant_id,
                WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
                Workflow.archived_at.is_(None),
                Workflow.department_id.is_not(None),
            )
        ).all()

        triggered_runs = 0
        now = datetime.now(UTC)
        for ev in events:
            conn = db.scalar(select(Connection).where(Connection.tenant_id == tenant_id, Connection.id == ev.connection_id))
            if conn is None or conn.department_id is None:
                ev.status = EventStatus.IGNORED
                ev.processed_at = now
                continue
            trusted_dept_id = conn.department_id
            ev.department_id = trusted_dept_id
            if trusted_dept_id not in active_dept_ids:
                ev.status = EventStatus.IGNORED
                ev.error = "Department is inactive or archived."
                ev.processed_at = now
                continue
            raw_payload = dict(ev.payload or {})
            raw_payload.pop("tenant_id", None)
            raw_payload.pop("department_id", None)
            matched_any = False
            for ver, wf in pub_versions:
                if wf.department_id != trusted_dept_id or wf.department_id not in active_dept_ids:
                    continue
                if ver.trigger_connection_id and ver.trigger_connection_id != ev.connection_id:
                    continue
                if ver.trigger_event_type and ver.trigger_event_type != ev.event_type:
                    continue
                matched_any = True
                cls.start_run(
                    db=db,
                    tenant_id=tenant_id,
                    workflow_id=wf.id,
                    input_data=raw_payload,
                    trigger_source=TriggerSource.EVENT,
                    event_id=ev.id,
                )
                triggered_runs += 1
            ev.status = EventStatus.PROCESSED if matched_any else EventStatus.IGNORED
            ev.processed_at = now
        db.flush()
        return triggered_runs
