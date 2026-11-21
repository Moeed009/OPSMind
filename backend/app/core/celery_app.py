
from __future__ import annotations

import logging

from celery import Celery

from app.core.config import get_settings

logger = logging.getLogger("opsmind.celery")
settings = get_settings()

celery_app = Celery(
    "opsmind",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workflows.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=300,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "poll-all-active-connectors": {
            "task": "opsmind.poll_all_connectors",
            "schedule": 120.0,
        },
        "schedule-department-workflows": {
            "task": "opsmind.schedule_department_workflows",
            "schedule": 60.0,
        },
    },
)
