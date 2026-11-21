from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any, Literal

from app.ai.exceptions import AIMalformedResponseError, AIValidationError
from app.ai.providers.base import BaseAIProvider
from app.ai.schemas.ai_schemas import (
    AIGenerationResult,
    ModelTier,
    ProviderRequest,
)
from app.ai.usage.cost_calculator import CostCalculator


class GenerationService:
    """Generates validated email, Slack, and operational drafts without executing external actions."""

    @classmethod
    def generate_offline(
        cls,
        *,
        prompt: str,
        context: dict[str, Any] | None = None,
        format_type: Literal["email", "slack", "summary", "general"] = "general",
        correlation_id: str | None = None,
    ) -> AIGenerationResult:
        t0 = time.perf_counter()
        if not prompt or not prompt.strip():
            raise AIValidationError("Generation prompt cannot be empty.", correlation_id=correlation_id)

        docs = (context or {}).get("documents") if isinstance(context, dict) else None
        doc_titles = [
            d.get("title")
            for d in (docs or [])
            if isinstance(d, dict) and d.get("title")
        ]
        evidence_note = (
            f" Referenced documentation: {', '.join(str(t) for t in doc_titles)}."
            if doc_titles
            else ""
        )

        if format_type == "slack":
            draft = (
                f"*OpsMind Operational Draft*\n"
                f"> Request summary: {prompt.strip()[:160]}\n"
                f"Status: Pending review before dispatch.{evidence_note}"
            )
        else:
            draft = (
                "Hello,\n\n"
                "Thank you for contacting support. We have received your inquiry and reviewed the provided details."
                f"{evidence_note}\n\n"
                "A team member will follow up if any additional information is required.\n\n"
                "Best regards,\nOpsMind Operations Team"
            )

        p_tokens = BaseAIProvider.estimate_tokens(prompt)
        c_tokens = BaseAIProvider.estimate_tokens(draft)
        latency = max(1, int((time.perf_counter() - t0) * 1000))

        return AIGenerationResult(
            content=draft,
            model=ModelTier.OFFLINE.value,
            format_type=format_type,
            is_draft=True,
            requires_approval=True,
            approval_reason="Generated in offline deterministic mode; human review recommended.",
            confidence=0.90,
            latency_ms=latency,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=p_tokens + c_tokens,
            cost_usd=Decimal("0.000000"),
            is_estimated_cost=False,
            provider="offline",
            is_offline=True,
            correlation_id=correlation_id,
        )

    @classmethod
    async def generate_with_provider(
        cls,
        *,
        provider: BaseAIProvider,
        model: str,
        prompt: str,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        format_type: Literal["email", "slack", "summary", "general"] = "general",
        correlation_id: str | None = None,
    ) -> AIGenerationResult:
        if not prompt or not prompt.strip():
            raise AIValidationError(
                "Generation prompt cannot be empty.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )

        format_guidance = {
            "email": "Format as a polite, professional business email draft with a clear greeting and sign-off.",
            "slack": "Format as a concise, actionable Slack message draft using clean bullet points.",
            "summary": "Format as a concise operational summary grounded strictly in the provided context.",
            "general": "Format as a clear, professional operational response draft.",
        }[format_type]

        sys_instruction = (
            f"{system_prompt or 'You are OpsMind AI Operations Assistant.'}\n"
            f"{format_guidance}\n"
            "CRITICAL RULES:\n"
            "1. Only state facts supported by the input message or provided contextual evidence.\n"
            "2. Never invent policy numbers, refund guarantees, or legal commitments.\n"
            "3. Produce only the draft text; never output executable tool calls."
        )

        ctx_block = (
            f"\n\n<contextual_evidence>\n{json.dumps(context, indent=2, default=str)}\n</contextual_evidence>"
            if context
            else ""
        )
        full_prompt = f"{prompt.strip()}{ctx_block}"

        req = ProviderRequest(
            prompt=full_prompt,
            system_instruction=sys_instruction,
            model=model,
            temperature=0.2,
            max_output_tokens=2048,
            expect_json=False,
            correlation_id=correlation_id,
        )
        resp = await provider.generate(req)

        content = (resp.content or "").strip()
        if not content:
            raise AIMalformedResponseError(
                "Provider returned an empty generation draft.",
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

        return AIGenerationResult(
            content=content,
            model=model,
            format_type=format_type,
            is_draft=True,
            requires_approval=False,
            confidence=0.94,
            latency_ms=resp.latency_ms,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            total_tokens=resp.usage.total_tokens,
            cost_usd=cost_calc.cost_usd,
            is_estimated_cost=cost_calc.is_estimated,
            provider=provider.provider_name,
            is_offline=False,
            correlation_id=resp.correlation_id,
        )
