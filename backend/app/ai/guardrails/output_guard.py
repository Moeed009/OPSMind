from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.ai.exceptions import AIGuardrailBlockedError
from app.core.config import get_settings
from app.db.enums import GuardrailDecision
from app.workflows.guardrails import EnterpriseGuardrailEngine, GuardrailResult

logger = logging.getLogger("opsmind.ai.guardrails.output")

UNAUTHORIZED_TOOL_EXECUTION_PATTERNS = [
    re.compile(r"<\s*tool_call\s*>", re.IGNORECASE),
    re.compile(r"\bEXECUTE_ACTION\s*\(", re.IGNORECASE),
    re.compile(r"\bDROP\s+TABLE\b|\bDELETE\s+FROM\s+users\b", re.IGNORECASE),
]


class OutputGuardrail:
    """Validates and inspects AI model outputs before returning them to callers or workflows."""

    @classmethod
    def inspect_output(
        cls,
        ai_text: str,
        *,
        confidence: float | None = None,
        confidence_threshold: float | None = None,
        tenant_policy: dict[str, Any] | None = None,
        db: Session | None = None,
        tenant_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        run_step_id: uuid.UUID | None = None,
        correlation_id: str | None = None,
        raise_on_block: bool = True,
    ) -> GuardrailResult:
        settings = get_settings()
        policy = tenant_policy or {}
        eff_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else float(policy.get("confidence_threshold", settings.ai_confidence_threshold))
        )

        for pat in UNAUTHORIZED_TOOL_EXECUTION_PATTERNS:
            match = pat.search(ai_text or "")
            if match:
                blocked = GuardrailResult(
                    passed=False,
                    decision=GuardrailDecision.BLOCKED,
                    sanitized_text="",
                    rule_id="SEC-UNAUTHORIZED-TOOL-01",
                    reason=f"Unauthorized tool or command execution attempt in AI output: '{match.group(0)}'",
                    redacted_excerpt=(ai_text or "")[:150],
                )
                if db is not None and tenant_id is not None:
                    try:
                        EnterpriseGuardrailEngine.record_audit_event(
                            db=db,
                            tenant_id=tenant_id,
                            result=blocked,
                            run_id=run_id,
                            run_step_id=run_step_id,
                            layer="output",
                        )
                    except Exception:
                        pass
                if raise_on_block:
                    raise AIGuardrailBlockedError(
                        blocked.reason or "Blocked unauthorized tool call in AI output.",
                        rule_id=blocked.rule_id,
                        decision=GuardrailDecision.BLOCKED.value,
                        layer="output",
                        correlation_id=correlation_id,
                    )
                return blocked

        try:
            result = EnterpriseGuardrailEngine.evaluate_output(
                ai_response_text=ai_text,
                confidence=confidence,
                confidence_threshold=eff_threshold,
            )
        except Exception as exc:
            logger.error("Output guardrail evaluation failed (failing closed): %s", exc)
            blocked = GuardrailResult(
                passed=False,
                decision=GuardrailDecision.BLOCKED,
                sanitized_text="",
                rule_id="SEC-OUTPUT-FAILSAFE-01",
                reason="Critical output security check could not be completed safely.",
            )
            if raise_on_block:
                raise AIGuardrailBlockedError(
                    blocked.reason or "Output guardrail check failed.",
                    rule_id=blocked.rule_id,
                    decision=GuardrailDecision.BLOCKED.value,
                    layer="output",
                    correlation_id=correlation_id,
                ) from exc
            return blocked

        if db is not None and tenant_id is not None and result.decision is not None:
            try:
                EnterpriseGuardrailEngine.record_audit_event(
                    db=db,
                    tenant_id=tenant_id,
                    result=result,
                    run_id=run_id,
                    run_step_id=run_step_id,
                    layer="output",
                )
            except Exception as audit_exc:
                logger.warning("Failed to record output guardrail audit event: %s", audit_exc)

        if not result.passed and result.decision == GuardrailDecision.BLOCKED and raise_on_block:
            raise AIGuardrailBlockedError(
                result.reason or "Output blocked by security guardrail.",
                rule_id=result.rule_id,
                decision=GuardrailDecision.BLOCKED.value,
                layer="output",
                correlation_id=correlation_id,
            )

        return result
