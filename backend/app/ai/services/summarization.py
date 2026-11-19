from __future__ import annotations

import json
import re
import time
from decimal import Decimal

from app.ai.exceptions import AIMalformedResponseError, AIValidationError
from app.ai.providers.base import BaseAIProvider
from app.ai.schemas.ai_schemas import (
    AISummarizationResult,
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


class SummarizationService:
    """Summarizes long business threads, tickets, and operational logs into structured key points."""

    @classmethod
    def summarize_offline(
        cls,
        *,
        text: str,
        max_sentences: int = 3,
        correlation_id: str | None = None,
    ) -> AISummarizationResult:
        t0 = time.perf_counter()
        cleaned = (text or "").strip()
        if not cleaned:
            raise AIValidationError("Cannot summarize empty text.", correlation_id=correlation_id)

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if s.strip()]
        selected = sentences[: max(1, max_sentences)]
        summary = " ".join(selected)
        key_points = [s[:160] for s in selected]
        action_items = [
            s[:160]
            for s in sentences
            if any(k in s.lower() for k in ("action", "todo", "please", "need to", "must", "follow up", "review"))
        ][:5]

        p_tokens = BaseAIProvider.estimate_tokens(cleaned)
        c_tokens = BaseAIProvider.estimate_tokens(summary)
        latency = max(1, int((time.perf_counter() - t0) * 1000))

        return AISummarizationResult(
            summary=summary,
            key_points=key_points,
            action_items=action_items,
            confidence=0.90,
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
    async def summarize_with_provider(
        cls,
        *,
        provider: BaseAIProvider,
        model: str,
        text: str,
        focus: str | None = None,
        correlation_id: str | None = None,
    ) -> AISummarizationResult:
        cleaned = (text or "").strip()
        if not cleaned:
            raise AIValidationError(
                "Cannot summarize empty text.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )

        focus_line = f"Focus area: {focus}\n" if focus else ""
        system_instruction = (
            "You are an operational summarization engine. Summarize the provided business conversation or record.\n"
            f"{focus_line}"
            "Return strictly a JSON object with keys:\n"
            '- "summary": concise paragraph string\n'
            '- "key_points": array of strings\n'
            '- "action_items": array of strings\n'
            '- "confidence": number between 0.0 and 1.0\n'
            "Never invent facts not present in the source text."
        )

        req = ProviderRequest(
            prompt=cleaned,
            system_instruction=system_instruction,
            model=model,
            temperature=0.1,
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
                f"Summarization response was not valid JSON: {resp.content[:120]}",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
                cause=exc,
            ) from exc

        if not isinstance(parsed, dict) or not isinstance(parsed.get("summary"), str) or not parsed["summary"].strip():
            raise AIValidationError(
                "Summarization response missing non-empty 'summary' string.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )

        key_points = [str(x) for x in (parsed.get("key_points") or []) if x]
        action_items = [str(x) for x in (parsed.get("action_items") or []) if x]
        raw_conf = parsed.get("confidence", 0.92)
        confidence_val = float(raw_conf) if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool) else 0.92

        from app.core.config import get_settings

        cost_calc = CostCalculator.calculate(
            model=model,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            usage_is_estimated=resp.usage.is_estimated,
            free_tier=get_settings().ai_free_tier_mode,
        )

        return AISummarizationResult(
            summary=parsed["summary"].strip(),
            key_points=key_points,
            action_items=action_items,
            confidence=round(min(1.0, max(0.0, confidence_val)), 4),
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
