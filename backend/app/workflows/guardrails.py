from __future__ import annotations

import base64
import binascii
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import GuardrailDecision
from app.db.models.guardrail_event import GuardrailEvent
from app.workflows.presidio_engine import PresidioPIIEngine

logger = logging.getLogger("opsmind.guardrails")

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|system)\s+(instructions|directives)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(in\s+)?(dan|developer\s+mode|unrestricted|god\s+mode)", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"system\s*override", re.IGNORECASE),
    re.compile(r"bypass\s+(all\s+)?(filters|guardrails|safety|restrictions)", re.IGNORECASE),
    re.compile(r"pretend\s+you\s+have\s+no\s+(rules|limits|guidelines)", re.IGNORECASE),
    re.compile(r"output\s+the\s+(entire\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"reveal\s+your\s+(secret\s+)?instructions", re.IGNORECASE),
    re.compile(r"<\s*system\s*>|\[\s*INST\s*\]|---\s*BEGIN\s+SYSTEM\s*---|```\s*system", re.IGNORECASE),
]

BASE64_CANDIDATE = re.compile(r"(?:[A-Za-z0-9+/]{4}){4,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")

API_KEY_PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,255}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{10,72}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED_JWT]"),
    (re.compile(r"\b(bearer\s+)[a-zA-Z0-9_\-\.]{20,}\b", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
]

CC_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b")
PHONE_PATTERN = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

def _luhn_checksum_valid(number_str: str) -> bool:
    """Verifies credit card numbers via the Luhn formula to avoid false positives."""
    digits = [int(c) for c in number_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = d * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0

LEGAL_KEYWORDS = [
    re.compile(r"\b(lawsuit|attorney|lawyer|sue\s+you|court\s+action|litigation|legal\s+notice)\b", re.IGNORECASE),
    re.compile(r"\b(regulatory\s+complaint|sec\s+complaint|ftc\s+violation|gdpr\s+fine)\b", re.IGNORECASE),
    re.compile(r"\b(consumer\s+protection\s+bureau|better\s+business\s+bureau)\b", re.IGNORECASE),
]

UNAUTHORIZED_PROMISES = [
    re.compile(r"\b(we\s+guarantee\s+a\s+100%\s+refund)\b", re.IGNORECASE),
    re.compile(r"\b(admit\s+full\s+liability)\b", re.IGNORECASE),
    re.compile(r"\b(our\s+system\s+was\s+hacked)\b", re.IGNORECASE),
]

FINANCIAL_CAP_PATTERN = re.compile(
    r"\$\s*(?:[5-9]\d{2,}|[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{3,})(?:\.\d{2})?"
)

@dataclass
class GuardrailResult:
    passed: bool
    decision: GuardrailDecision | None = None
    sanitized_text: str = ""
    rule_id: str | None = None
    reason: str | None = None
    redacted_excerpt: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

class EnterpriseGuardrailEngine:
    """Multi-stage enterprise guardrail engine evaluating inputs and AI outputs against security rules."""

    @classmethod
    def evaluate_input(cls, raw_text: str, enable_presidio: bool = True) -> GuardrailResult:
        """Inspects incoming user or connector text before passing it to AI models."""
        if not raw_text or not raw_text.strip():
            return GuardrailResult(passed=True, sanitized_text=raw_text)

        for pat in INJECTION_PATTERNS:
            match = pat.search(raw_text)
            if match:
                matched_str = match.group(0)
                return GuardrailResult(
                    passed=False,
                    decision=GuardrailDecision.BLOCKED,
                    sanitized_text="",
                    rule_id="SEC-INJECTION-DIRECT-01",
                    reason=f"Direct prompt injection signature detected: '{matched_str}'",
                    redacted_excerpt=raw_text[:200],
                    details={"matched_pattern": matched_str},
                )

        for b64_match in BASE64_CANDIDATE.finditer(raw_text):
            candidate = b64_match.group(0)
            try:
                decoded = base64.b64decode(candidate, validate=True).decode("utf-8", errors="ignore")
                for pat in INJECTION_PATTERNS:
                    if pat.search(decoded):
                        return GuardrailResult(
                            passed=False,
                            decision=GuardrailDecision.BLOCKED,
                            sanitized_text="",
                            rule_id="SEC-INJECTION-OBFUSCATED-02",
                            reason="Base64 obfuscated prompt injection payload detected",
                            redacted_excerpt=candidate[:100],
                            details={"decoded_sample": decoded[:100]},
                        )
            except (binascii.Error, UnicodeDecodeError):
                pass

        sanitized = raw_text
        redactions = []

        for pat, replacement in API_KEY_PATTERNS:
            if pat.search(sanitized):
                redactions.append("API/Secret Tokens")
                sanitized = pat.sub(replacement, sanitized)

        for cc_match in CC_PATTERN.finditer(sanitized):
            candidate = cc_match.group(0)
            clean_digits = re.sub(r"\D", "", candidate)
            if _luhn_checksum_valid(clean_digits):
                redactions.append("Credit Card Number")
                sanitized = sanitized.replace(candidate, "[REDACTED_CREDIT_CARD]")

        if SSN_PATTERN.search(sanitized):
            redactions.append("Social Security Number")
            sanitized = SSN_PATTERN.sub("[REDACTED_SSN]", sanitized)

        if enable_presidio:
            presidio_res = PresidioPIIEngine.sanitize(sanitized)
            if presidio_res.has_pii:
                sanitized = presidio_res.anonymized_text
                redactions.extend(presidio_res.detected_entities)

        if redactions:
            return GuardrailResult(
                passed=True,
                decision=GuardrailDecision.MASKED,
                sanitized_text=sanitized,
                rule_id="PRIV-PII-REDACTED-01",
                reason=f"PII elements automatically sanitized: {', '.join(sorted(list(set(redactions))))}",
                redacted_excerpt=sanitized[:200],
                details={"redactions": sorted(list(set(redactions)))},
            )

        return GuardrailResult(passed=True, sanitized_text=raw_text)

    @classmethod
    def evaluate_output(
        cls,
        ai_response_text: str,
        confidence: float | None = None,
        confidence_threshold: float = 0.88,
    ) -> GuardrailResult:
        """Inspects AI-generated content before external dispatch."""
        if not ai_response_text:
            return GuardrailResult(passed=True, sanitized_text="")

        if confidence is not None and confidence < confidence_threshold:
            return GuardrailResult(
                passed=False,
                decision=GuardrailDecision.ESCALATED,
                sanitized_text=ai_response_text,
                rule_id="QUAL-CONFIDENCE-LOW-01",
                reason=f"AI confidence score ({confidence:.2f}) falls below safety threshold ({confidence_threshold:.2f})",
                redacted_excerpt=ai_response_text[:200],
                details={"confidence": confidence, "threshold": confidence_threshold},
            )

        for legal_pat in LEGAL_KEYWORDS:
            match = legal_pat.search(ai_response_text)
            if match:
                return GuardrailResult(
                    passed=False,
                    decision=GuardrailDecision.ESCALATED,
                    sanitized_text=ai_response_text,
                    rule_id="BRAND-LEGAL-LITIGATION-01",
                    reason=f"Legal dispute terms detected ('{match.group(0)}'); escalating to human reviewer",
                    redacted_excerpt=ai_response_text[:200],
                    details={"matched_term": match.group(0)},
                )

        fin_match = FINANCIAL_CAP_PATTERN.search(ai_response_text)
        if fin_match:
            return GuardrailResult(
                passed=False,
                decision=GuardrailDecision.ESCALATED,
                sanitized_text=ai_response_text,
                rule_id="POLICY-FINANCIAL-CAP-01",
                reason=f"Financial commitment exceeds threshold ({fin_match.group(0)}); requiring supervisor approval",
                redacted_excerpt=ai_response_text[:200],
                details={"amount_detected": fin_match.group(0)},
            )

        for promise_pat in UNAUTHORIZED_PROMISES:
            match = promise_pat.search(ai_response_text)
            if match:
                return GuardrailResult(
                    passed=False,
                    decision=GuardrailDecision.BLOCKED,
                    sanitized_text=ai_response_text,
                    rule_id="BRAND-UNAUTHORIZED-COMMIT-01",
                    reason=f"Unauthorized contractual commitment detected: '{match.group(0)}'",
                    redacted_excerpt=ai_response_text[:200],
                    details={"matched_promise": match.group(0)},
                )

        for pat, _ in API_KEY_PATTERNS:
            if pat.search(ai_response_text):
                return GuardrailResult(
                    passed=False,
                    decision=GuardrailDecision.BLOCKED,
                    sanitized_text="",
                    rule_id="SEC-LEAK-PREVENTION-01",
                    reason="Outgoing AI response contained an internal secret or authorization token",
                    redacted_excerpt="[SECRET LEAK BLOCKED]",
                )

        return GuardrailResult(passed=True, sanitized_text=ai_response_text)

    @classmethod
    def record_audit_event(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        result: GuardrailResult,
        run_id: uuid.UUID | None = None,
        run_step_id: uuid.UUID | None = None,
        layer: str = "input",
    ) -> GuardrailEvent | None:
        """Persists flagged guardrail interventions into the PostgreSQL guardrail_events table."""
        if not result.decision:
            return None

        event = GuardrailEvent(
            tenant_id=tenant_id,
            run_id=run_id,
            run_step_id=run_step_id,
            layer=layer,
            rule_id=result.rule_id or "GUARDRAIL-GENERIC",
            decision=result.decision,
            reason=result.reason or "Policy intervention applied",
            excerpt_redacted=result.redacted_excerpt,
            details=result.details,
            created_at=datetime.now(UTC),
        )
        db.add(event)
        db.flush()
        return event
