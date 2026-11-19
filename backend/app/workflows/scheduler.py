"""Department-timezone-aware Celery Beat scheduler with DST & missed-run grace handling."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.enums import DepartmentStatus, RunStatus, TriggerSource, WorkflowVersionStatus
from app.db.models import Department, Run, Workflow, WorkflowVersion
from app.workflows.definition import ScheduleTrigger


def _cron_token_matches(token: str, value: int) -> bool:
    for part in token.split(","):
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
        else:
            base, step = part, 1
        if base == "*":
            if value % step == 0:
                return True
        elif "-" in base:
            lo, hi = (int(x) for x in base.split("-", 1))
            if lo <= value <= hi and (value - lo) % step == 0:
                return True
        elif int(base) == value:
            return True
    return False


def _cron_matches_local(cron: str, local_dt: datetime) -> bool:
    minute_f, hour_f, dom_f, month_f, dow_f = cron.split()
    # Python weekday: Mon=0..Sun=6; cron dow: Sun=0 or 7, Mon=1..Sat=6
    cron_dow = (local_dt.weekday() + 1) % 7
    dow_ok = _cron_token_matches(dow_f, cron_dow) or (cron_dow == 0 and _cron_token_matches(dow_f, 7))
    return (
        _cron_token_matches(minute_f, local_dt.minute)
        and _cron_token_matches(hour_f, local_dt.hour)
        and _cron_token_matches(dom_f, local_dt.day)
        and _cron_token_matches(month_f, local_dt.month)
        and dow_ok
    )


def compute_due_slots(
    trigger: ScheduleTrigger,
    tz_name: str,
    now_utc: datetime,
    *,
    grace_minutes: int = 15,
) -> list[datetime]:
    """Compute due UTC scheduled_for slots inside (now_utc - grace, now_utc] in department timezone."""
    tz = ZoneInfo(tz_name)
    now_utc_min = now_utc.astimezone(UTC).replace(second=0, microsecond=0)
    window_start = now_utc_min - timedelta(minutes=grace_minutes)

    due: list[datetime] = []
    # Walk minute-by-minute across the small grace window (handles DST fold/gap cleanly via UTC->local)
    for offset in range(grace_minutes):
        cand_utc = now_utc_min - timedelta(minutes=offset)
        if cand_utc <= window_start:
            continue
        local_dt = cand_utc.astimezone(tz)

        if trigger.mode == "daily" and trigger.time:
            hh, mm = (int(x) for x in trigger.time.split(":"))
            if local_dt.hour == hh and local_dt.minute == mm:
                due.append(cand_utc)
        elif trigger.mode == "weekly" and trigger.time and trigger.weekdays is not None:
            hh, mm = (int(x) for x in trigger.time.split(":"))
            if local_dt.weekday() in trigger.weekdays and local_dt.hour == hh and local_dt.minute == mm:
                due.append(cand_utc)
        elif trigger.mode == "interval" and trigger.every_minutes:
            epoch_min = int(cand_utc.timestamp() // 60)
            if epoch_min % trigger.every_minutes == 0:
                due.append(cand_utc)
        elif trigger.mode == "cron" and trigger.cron:
            if _cron_matches_local(trigger.cron, local_dt):
                due.append(cand_utc)

    return sorted(due)


def enqueue_due_scheduled_runs(
    db: Session,
    *,
    now_utc: datetime | None = None,
    grace_minutes: int = 15,
) -> list[Run]:
    """Scan active scheduled workflows and idempotently enqueue due runs."""
    now = now_utc or datetime.now(UTC)
    rows = db.execute(
        select(Workflow, WorkflowVersion, Department)
        .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
        .join(Department, Department.id == Workflow.department_id)
        .where(
            Workflow.archived_at.is_(None),
            WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
            Department.status == DepartmentStatus.ACTIVE,
        )
    ).all()

    enqueued: list[Run] = []
    for wf, ver, dept in rows:
        trig_raw = (ver.definition or {}).get("trigger")
        if not isinstance(trig_raw, dict) or trig_raw.get("type") != "schedule":
            continue
        trigger = ScheduleTrigger.model_validate(trig_raw)
        slots = compute_due_slots(trigger, dept.timezone, now, grace_minutes=grace_minutes)
        for slot in slots:
            sp = db.begin_nested()
            try:
                run = Run(
                    tenant_id=wf.tenant_id,
                    department_id=dept.id,
                    workflow_id=wf.id,
                    workflow_version_id=ver.id,
                    trigger_source=TriggerSource.SCHEDULE,
                    status=RunStatus.QUEUED,
                    scheduled_for=slot,
                    trace_id=uuid.uuid4().hex,
                    input={"scheduled_for": slot.isoformat()},
                )
                db.add(run)
                db.flush()
                sp.commit()
                enqueued.append(run)
            except IntegrityError:
                sp.rollback()
    return enqueued
