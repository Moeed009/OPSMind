from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from typing import Any

from app.ai.exceptions import AIMalformedResponseError, AIValidationError
from app.ai.providers.base import BaseAIProvider
from app.ai.schemas.ai_schemas import (
    AIExtractionResult,
    FieldSpec,
    ModelTier,
    ProviderRequest,
)
from app.ai.usage.cost_calculator import CostCalculator


def _strip_fences(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _normalize_field_specs(fields: list[str | FieldSpec | dict[str, Any]]) -> list[FieldSpec]:
    specs: list[FieldSpec] = []
    for item in fields:
        if isinstance(item, FieldSpec):
            specs.append(item)
        elif isinstance(item, dict):
            specs.append(FieldSpec.model_validate(item))
        elif isinstance(item, str) and item.strip():
            specs.append(FieldSpec(name=item.strip()))
    return specs


def _validate_field_type(field_name: str, value: Any, expected_type: str) -> Any:
    if value is None:
        return None
    if expected_type == "string":
        if isinstance(value, (dict, list)):
            raise ValueError(f"Field '{field_name}' expected string, got {type(value).__name__}")
        return str(value)
    if expected_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if isinstance(value, str):
                cleaned = re.sub(r"[$,\s]", "", value)
                return float(cleaned)
            raise ValueError(f"Field '{field_name}' expected number, got {type(value).__name__}")
        return float(value)
    if expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                return int(value.strip())
            raise ValueError(f"Field '{field_name}' expected integer, got {type(value).__name__}")
        return int(value)
    if expected_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"Field '{field_name}' expected boolean, got {type(value).__name__}")
        return value
    if expected_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"Field '{field_name}' expected array, got {type(value).__name__}")
        return value
    if expected_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"Field '{field_name}' expected object, got {type(value).__name__}")
        return value
    return value


class ExtractionService:
    """Extracts structured fields from messages/documents without fabricating missing values."""

    @classmethod
    def extract_offline(
        cls,
        *,
        text: str,
        fields: list[str | FieldSpec | dict[str, Any]],
        correlation_id: str | None = None,
    ) -> AIExtractionResult:
        """Rule-based extraction from actual input text only; missing values are None (never fabricated)."""
        t0 = time.perf_counter()
        specs = _normalize_field_specs(fields or ["title", "summary"])
        extracted: dict[str, Any] = {}
        evidence: dict[str, str | None] = {}
        missing_required: list[str] = []

        raw = (text or "").strip()
        lower = raw.lower()
        sentences = [s.strip() for s in re.split(r"[.\n]+", raw) if s.strip()]

        for spec in specs:
            fname = spec.name
            fl = fname.lower()
            val: Any = None
            ev: str | None = None

            if fl in ("order_id", "invoice_id", "ticket_id", "reference_id"):
                m = re.search(r"(#?[A-Z]{2,5}-\d{2,10}|#\d{3,10}|\bINV-\d+\b|\bORD-\d+\b)", raw, re.IGNORECASE)
                if m:
                    val = m.group(1)
                    ev = m.group(0)
            elif "amount" in fl or "price" in fl or "total" in fl:
                m = re.search(r"\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)", raw)
                if m:
                    num_str = m.group(1).replace(",", "")
                    val = float(num_str) if spec.field_type in ("number", "integer") else m.group(0)
                    ev = m.group(0)
            elif "currency" in fl:
                if "$" in raw or "usd" in lower:
                    val = "USD"
                    ev = "$" if "$" in raw else "USD"
                elif "eur" in lower or "€" in raw:
                    val = "EUR"
                    ev = "EUR"
            elif "email" in fl:
                m = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b", raw)
                if m:
                    val = m.group(0)
                    ev = m.group(0)
            elif "name" in fl or "customer" in fl:
                m = re.search(
                    r"(?:customer|from|name|client)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
                    raw,
                )
                if m:
                    val = m.group(1).strip()
                    ev = m.group(0).strip()
            elif "urgency" in fl or "priority" in fl:
                if any(w in lower for w in ("immediately", "urgent", "critical", "asap", "emergency", "twice")):
                    val = "high"
                    ev = "urgent/immediate keyword in text"
                elif any(w in lower for w in ("soon", "important", "discrepancy", "issue", "refund")):
                    val = "medium"
                    ev = "operational issue keyword in text"
                elif raw:
                    val = "low"
                    ev = raw[:60]
            elif fl == "title":
                if sentences:
                    val = sentences[0][:120]
                    ev = sentences[0][:120]
            elif fl in ("summary", "issue_summary", "reason"):
                if raw:
                    val = raw[:200]
                    ev = raw[:200]
            elif fl == "action_items":
                if len(sentences) > 1:
                    val = sentences[1:] if spec.field_type == "array" else "; ".join(sentences[1:])
                    ev = "; ".join(sentences[1:])[:120]
                elif sentences:
                    val = [sentences[0]] if spec.field_type == "array" else sentences[0]
                    ev = sentences[0][:120]

            extracted[fname] = val
            evidence[fname] = ev
            if spec.required and val is None:
                missing_required.append(fname)

        non_null_count = sum(1 for v in extracted.values() if v is not None)
        total_fields = max(1, len(specs))
        confidence = round(0.90 + 0.05 * (non_null_count / total_fields), 4) if non_null_count > 0 else 0.50

        p_tokens = BaseAIProvider.estimate_tokens(raw)
        c_tokens = 25
        latency = max(1, int((time.perf_counter() - t0) * 1000))

        return AIExtractionResult(
            extracted=extracted,
            evidence=evidence,
            missing_required_fields=missing_required,
            confidence=confidence,
            latency_ms=latency,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=p_tokens + c_tokens,
            cost_usd=Decimal("0.000000"),
            is_estimated_cost=False,
            model_used=ModelTier.OFFLINE.value,
            provider="offline",
            is_offline=True,
            correlation_id=correlation_id,
        )

    @classmethod
    async def extract_with_provider(
        cls,
        *,
        provider: BaseAIProvider,
        model: str,
        text: str,
        fields: list[str | FieldSpec | dict[str, Any]],
        prompt: str | None = None,
        strict_required: bool = False,
        correlation_id: str | None = None,
    ) -> AIExtractionResult:
        specs = _normalize_field_specs(fields)
        if not specs:
            raise AIValidationError(
                "At least one field specification must be provided for extraction.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )

        field_descriptions = [
            f'- "{s.name}" (type: {s.field_type}, {"required" if s.required else "optional"})'
            + (f": {s.description}" if s.description else "")
            for s in specs
        ]
        system_instruction = (
            "You are a precision data extraction engine. Extract the requested fields strictly from the provided text.\n"
            "CRITICAL RULES:\n"
            "1. Never fabricate, guess, or invent customer names, invoice IDs, order IDs, or numbers not present in the text.\n"
            "2. If a field is not explicitly present in the text, set its value to null.\n"
            "3. Return a JSON object with keys:\n"
            '   - "extracted": object mapping each requested field name to its extracted value or null\n'
            '   - "evidence": object mapping each field name to the exact supporting quote from the text or null\n'
            '   - "confidence": number between 0.0 and 1.0\n'
            "Requested fields:\n" + "\n".join(field_descriptions)
        )
        user_prompt = f"{prompt or 'Extract the requested fields from this document:'}\n\n{text}"

        req = ProviderRequest(
            prompt=user_prompt,
            system_instruction=system_instruction,
            model=model,
            temperature=0.0,
            max_output_tokens=1024,
            expect_json=True,
            response_schema={"type": "OBJECT"},
            correlation_id=correlation_id,
        )
        resp = await provider.generate(req)

        try:
            parsed = json.loads(_strip_fences(resp.content))
        except (ValueError, json.JSONDecodeError) as exc:
            raise AIMalformedResponseError(
                f"Extraction response was not valid JSON: {resp.content[:120]}",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
                cause=exc,
            ) from exc

        if not isinstance(parsed, dict):
            raise AIMalformedResponseError(
                "Extraction JSON response must be an object.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )

        raw_extracted = parsed.get("extracted") if isinstance(parsed.get("extracted"), dict) else parsed
        raw_evidence = parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {}
        raw_conf = parsed.get("confidence")

        if raw_conf is not None:
            if isinstance(raw_conf, bool) or not isinstance(raw_conf, (int, float)):
                raise AIValidationError(
                    "Extraction 'confidence' must be numeric when present.",
                    provider=provider.provider_name,
                    model=model,
                    correlation_id=correlation_id,
                )
            confidence_val = float(raw_conf)
            if not (0.0 <= confidence_val <= 1.0):
                raise AIValidationError(
                    f"Extraction confidence {confidence_val} out of range [0.0, 1.0].",
                    provider=provider.provider_name,
                    model=model,
                    correlation_id=correlation_id,
                )
        else:
            confidence_val = 0.90

        validated_extracted: dict[str, Any] = {}
        validated_evidence: dict[str, str | None] = {}
        missing_required: list[str] = []

        for spec in specs:
            raw_val = raw_extracted.get(spec.name)
            try:
                typed_val = _validate_field_type(spec.name, raw_val, spec.field_type)
            except ValueError as exc:
                raise AIValidationError(
                    str(exc),
                    provider=provider.provider_name,
                    model=model,
                    correlation_id=correlation_id,
                    cause=exc,
                ) from exc

            validated_extracted[spec.name] = typed_val
            ev_val = raw_evidence.get(spec.name)
            validated_evidence[spec.name] = str(ev_val) if isinstance(ev_val, str) else None

            if spec.required and typed_val is None:
                missing_required.append(spec.name)

        if strict_required and missing_required:
            raise AIValidationError(
                f"Missing required extracted fields: {', '.join(missing_required)}",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )

        from app.core.config import get_settings

        cost_calc = CostCalculator.calculate(
            model=model,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            usage_is_estimated=resp.usage.is_estimated,
            free_tier=get_settings().ai_free_tier_mode,
        )

        return AIExtractionResult(
            extracted=validated_extracted,
            evidence=validated_evidence,
            missing_required_fields=missing_required,
            confidence=round(confidence_val, 4),
            latency_ms=resp.latency_ms,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            total_tokens=resp.usage.total_tokens,
            cost_usd=cost_calc.cost_usd,
            is_estimated_cost=cost_calc.is_estimated,
            model_used=model,
            provider=provider.provider_name,
            is_offline=False,
            correlation_id=resp.correlation_id,
        )
