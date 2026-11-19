"""Tenant-isolated AI usage tracker and concurrent budget limit enforcer."""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.exceptions import AIUsageLimitExceededError
from app.ai.usage.cost_calculator import CostCalculator
from app.core.config import get_settings
from app.db.enums import UsageMetric
from app.db.models.tenant import Tenant
from app.db.models.usage_record import UsageRecord

logger = logging.getLogger("opsmind.ai.usage")

_TENANT_LOCKS: dict[str, threading.Lock] = {}
_GLOBAL_LOCK = threading.Lock()
_IN_MEMORY_SPEND: dict[str, Decimal] = {}
_IN_MEMORY_CAP: dict[str, Decimal] = {}


def _get_tenant_lock(tenant_id: uuid.UUID | str) -> threading.Lock:
    key = str(tenant_id)
    with _GLOBAL_LOCK:
        lock = _TENANT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _TENANT_LOCKS[key] = lock
        return lock


class UsageTracker:
    """Tracks AI token/cost telemetry per tenant and enforces monthly budget caps safely under concurrency."""

    @classmethod
    def set_in_memory_cap(cls, tenant_id: uuid.UUID | str, cap_usd: Decimal | float | str) -> None:
        key = str(tenant_id)
        with _get_tenant_lock(key):
            _IN_MEMORY_CAP[key] = Decimal(str(cap_usd))
            _IN_MEMORY_SPEND.setdefault(key, Decimal("0.000000"))

    @classmethod
    def reset_in_memory_state(cls, tenant_id: uuid.UUID | str | None = None) -> None:
        with _GLOBAL_LOCK:
            if tenant_id is None:
                _IN_MEMORY_SPEND.clear()
                _IN_MEMORY_CAP.clear()
            else:
                k = str(tenant_id)
                _IN_MEMORY_SPEND.pop(k, None)
                _IN_MEMORY_CAP.pop(k, None)

    @classmethod
    def get_monthly_spend(cls, db: Session | None, tenant_id: uuid.UUID) -> tuple[Decimal, Decimal]:
        """Returns (current_month_spend_usd, monthly_cap_usd) for the tenant."""
        settings = get_settings()
        default_cap = Decimal(str(settings.ai_default_monthly_budget_usd))
        key = str(tenant_id)

        if db is None:
            return _IN_MEMORY_SPEND.get(key, Decimal("0.000000")), _IN_MEMORY_CAP.get(key, default_cap)

        tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        cap = (
            Decimal(str(tenant.monthly_ai_cost_cap_usd))
            if tenant and tenant.monthly_ai_cost_cap_usd is not None
            else _IN_MEMORY_CAP.get(key, default_cap)
        )

        now = datetime.now(UTC)
        month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        spent = db.scalar(
            select(func.coalesce(func.sum(UsageRecord.cost_usd), Decimal("0.000000"))).where(
                UsageRecord.tenant_id == tenant_id,
                UsageRecord.metric == UsageMetric.AI_TOKENS,
                UsageRecord.occurred_at >= month_start,
            )
        )
        return Decimal(str(spent or 0)), cap

    @classmethod
    def check_budget(
        cls,
        *,
        tenant_id: uuid.UUID | None,
        department_id: uuid.UUID | None = None,
        db: Session | None = None,
        estimated_cost_usd: Decimal = Decimal("0.000000"),
        correlation_id: str | None = None,
    ) -> None:
        if tenant_id is None:
            return
        lock = _get_tenant_lock(tenant_id)
        with lock:
            spent, cap = cls.get_monthly_spend(db, tenant_id)
            if spent + estimated_cost_usd > cap:
                raise AIUsageLimitExceededError(
                    f"Tenant {tenant_id} exceeded monthly AI budget (${spent:.6f} + ${estimated_cost_usd:.6f} > ${cap:.4f}).",
                    tenant_id=str(tenant_id),
                    current_spend_usd=f"{spent:.6f}",
                    limit_usd=f"{cap:.4f}",
                    correlation_id=correlation_id,
                )
            if department_id is not None and db is not None:
                from app.db.models.department import Department

                dept = db.scalar(
                    select(Department).where(
                        Department.tenant_id == tenant_id, Department.id == department_id
                    )
                )
                if dept and dept.monthly_ai_cost_cap_usd is not None:
                    now = datetime.now(UTC)
                    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
                    dept_spent = Decimal(
                        str(
                            db.scalar(
                                select(
                                    func.coalesce(func.sum(UsageRecord.cost_usd), Decimal("0.000000"))
                                ).where(
                                    UsageRecord.tenant_id == tenant_id,
                                    UsageRecord.department_id == department_id,
                                    UsageRecord.metric == UsageMetric.AI_TOKENS,
                                    UsageRecord.occurred_at >= month_start,
                                )
                            )
                            or 0
                        )
                    )
                    effective_dept_cap = min(Decimal(str(dept.monthly_ai_cost_cap_usd)), cap)
                    if dept_spent + estimated_cost_usd > effective_dept_cap:
                        raise AIUsageLimitExceededError(
                            f"Department {department_id} exceeded monthly AI budget (${dept_spent:.6f} + ${estimated_cost_usd:.6f} > ${effective_dept_cap:.4f}).",
                            tenant_id=str(tenant_id),
                            current_spend_usd=f"{dept_spent:.6f}",
                            limit_usd=f"{effective_dept_cap:.4f}",
                            correlation_id=correlation_id,
                        )

    @classmethod
    def record_usage(
        cls,
        *,
        tenant_id: uuid.UUID | None,
        provider: str,
        model: str,
        task_type: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        status: str = "succeeded",
        usage_is_estimated: bool = False,
        db: Session | None = None,
        run_id: uuid.UUID | None = None,
        workflow_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        source_key: str | None = None,
        correlation_id: str | None = None,
        enforce_limit: bool = True,
    ) -> tuple[Decimal, bool, UsageRecord | None]:
        has_explicit_cap = tenant_id is not None and str(tenant_id) in _IN_MEMORY_CAP
        is_free = get_settings().ai_free_tier_mode and not has_explicit_cap
        calc = CostCalculator.calculate(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usage_is_estimated=usage_is_estimated,
            free_tier=is_free,
        )
        cost_usd = calc.cost_usd if status == "succeeded" else Decimal("0.000000")

        if tenant_id is None:
            return cost_usd, calc.is_estimated, None

        lock = _get_tenant_lock(tenant_id)
        with lock:
            spent, cap = cls.get_monthly_spend(db, tenant_id)
            if enforce_limit and status == "succeeded" and (spent + cost_usd > cap):
                raise AIUsageLimitExceededError(
                    f"Tenant {tenant_id} exceeded monthly AI budget (${spent:.6f} + ${cost_usd:.6f} > ${cap:.4f}).",
                    tenant_id=str(tenant_id),
                    current_spend_usd=f"{spent:.6f}",
                    limit_usd=f"{cap:.4f}",
                    correlation_id=correlation_id,
                )

            key = str(tenant_id)
            if status == "succeeded":
                _IN_MEMORY_SPEND[key] = _IN_MEMORY_SPEND.get(key, Decimal("0.000000")) + cost_usd

            if db is None:
                return cost_usd, calc.is_estimated, None

            eff_source_key = source_key or f"ai:{correlation_id or uuid.uuid4().hex}:{uuid.uuid4().hex[:8]}"
            record = UsageRecord(
                tenant_id=tenant_id,
                department_id=department_id,
                metric=UsageMetric.AI_TOKENS,
                quantity=Decimal(calc.total_tokens),
                cost_usd=cost_usd,
                source_key=eff_source_key,
                run_id=run_id,
                workflow_id=workflow_id,
                provider=provider,
                model=model,
                task_type=task_type,
                prompt_tokens=calc.prompt_tokens,
                completion_tokens=calc.completion_tokens,
                total_tokens=calc.total_tokens,
                latency_ms=latency_ms,
                status=status,
                is_estimated_cost=calc.is_estimated,
                correlation_id=correlation_id,
                occurred_at=datetime.now(UTC),
            )
            db.add(record)
            db.flush()
            return cost_usd, calc.is_estimated, record
