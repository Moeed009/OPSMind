from __future__ import annotations

import json
import re
import time
from decimal import Decimal

from app.ai.exceptions import AIMalformedResponseError, AIValidationError
from app.ai.providers.base import BaseAIProvider
from app.ai.schemas.ai_schemas import (
    AIClassificationResult,
    ModelTier,
    ProviderRequest,
)
from app.ai.usage.cost_calculator import CostCalculator


def _strip_markdown_fences(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


class ClassificationService:
    """Classifies business messages into configured categories with validation."""

    UNCERTAIN_CATEGORY = "Uncertain"

    @classmethod
    def classify_offline(
        cls,
        *,
        text: str,
        categories: list[str],
        correlation_id: str | None = None,
    ) -> AIClassificationResult:
        t0 = time.perf_counter()
        categories_clean = [c.strip() for c in categories if c and c.strip()] or [
            "General Support",
            "Billing",
            "Bug Report",
            "Feature Request",
        ]
        lower = (text or "").lower()
        matched_cat: str | None = None
        matched_Hits = 0

        keyword_groups = [
            (("invoice", "bill", "refund", "charge", "payment", "dispute", "credit"), ("bill", "refund", "payment", "finance", "invoice")),
            (("error", "bug", "broken", "fail", "crash", "outage", "exception"), ("bug", "tech", "issue", "support", "incident")),
            (("feature", "add", "suggestion", "improve", "request", "roadmap"), ("feature", "enhancement", "product")),
            (("price", "quote", "demo", "purchase", "upgrade", "enterprise"), ("sales", "pricing", "commercial")),
        ]

        for c in categories_clean:
            c_low = c.lower()
            for text_keys, cat_keys in keyword_groups:
                hits = sum(1 for k in text_keys if k in lower)
                if hits > 0 and any(ck in c_low for ck in cat_keys):
                    matched_cat = c
                    matched_Hits = hits
                    break
            if matched_cat:
                break

        if matched_cat is None:
            for c in categories_clean:
                words = [w for w in re.split(r"\W+", c.lower()) if len(w) >= 4]
                if any(w in lower for w in words):
                    matched_cat = c
                    matched_Hits = 1
                    break

        is_uncertain = matched_cat is None
        final_cat = matched_cat if matched_cat is not None else categories_clean[0]
        # Deterministic rule score based on actual keyword matches (never fabricated as a live LLM score)
        confidence = min(0.95, 0.85 + 0.03 * matched_Hits) if not is_uncertain else 0.55

        prompt_tokens = BaseAIProvider.estimate_tokens(text)
        completion_tokens = 20
        latency = max(1, int((time.perf_counter() - t0) * 1000))

        return AIClassificationResult(
            category=final_cat,
            confidence=round(confidence, 4),
            reasoning=(
                f"Rule-based offline match for '{final_cat}' ({matched_Hits} keyword hit(s))."
                if not is_uncertain
                else f"No keyword match found; defaulted to '{final_cat}' with uncertain flag."
            ),
            is_uncertain=is_uncertain,
            latency_ms=latency,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=Decimal("0.000000"),
            is_estimated_cost=False,
            model_used=ModelTier.OFFLINE.value,
            provider="offline",
            is_offline=True,
            correlation_id=correlation_id,
        )

    @classmethod
    async def classify_with_provider(
        cls,
        *,
        provider: BaseAIProvider,
        model: str,
        text: str,
        categories: list[str],
        prompt: str | None = None,
        confidence_threshold: float = 0.65,
        correlation_id: str | None = None,
    ) -> AIClassificationResult:
        categories_clean = [c.strip() for c in categories if c and c.strip()]
        if not categories_clean:
            raise AIValidationError(
                "At least one target category must be provided for classification.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )

        allowed_list = ", ".join(f'"{c}"' for c in categories_clean)
        system_instruction = (
            "You are an enterprise operations classifier. "
            f"Classify the message into exactly ONE of these allowed categories: [{allowed_list}], "
            f'or "{cls.UNCERTAIN_CATEGORY}" if none of the categories fit. '
            "Respond strictly with a JSON object containing keys: "
            '"category" (string), "confidence" (number between 0.0 and 1.0), and "reasoning" (non-empty string). '
            "Do not invent categories outside the allowed list."
        )
        user_prompt = f"{prompt or 'Classify the following business message:'}\n\n{text}"

        req = ProviderRequest(
            prompt=user_prompt,
            system_instruction=system_instruction,
            model=model,
            temperature=0.1,
            max_output_tokens=512,
            expect_json=True,
            response_schema={"type": "OBJECT"},
            correlation_id=correlation_id,
        )
        resp = await provider.generate(req)

        try:
            parsed = json.loads(_strip_markdown_fences(resp.content))
        except (ValueError, json.JSONDecodeError) as exc:
            raise AIMalformedResponseError(
                f"Classification response was not valid JSON: {resp.content[:120]}",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
                cause=exc,
            ) from exc

        if not isinstance(parsed, dict):
            raise AIMalformedResponseError(
                "Classification JSON response must be an object.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )

        raw_category = parsed.get("category")
        raw_confidence = parsed.get("confidence")
        raw_reasoning = parsed.get("reasoning")

        if not isinstance(raw_category, str) or not raw_category.strip():
            raise AIValidationError(
                "Classification response missing valid 'category' string.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )
        if raw_confidence is None or not isinstance(raw_confidence, (int, float)) or isinstance(raw_confidence, bool):
            raise AIValidationError(
                "Classification response missing numeric 'confidence' score.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )
        confidence_val = float(raw_confidence)
        if not (0.0 <= confidence_val <= 1.0):
            raise AIValidationError(
                f"Classification confidence {confidence_val} out of range [0.0, 1.0].",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )
        if not isinstance(raw_reasoning, str) or not raw_reasoning.strip():
            raise AIValidationError(
                "Classification response missing non-empty 'reasoning'.",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )

        canonical_map = {c.lower(): c for c in categories_clean}
        cat_clean = raw_category.strip()
        matched_canonical = canonical_map.get(cat_clean.lower())

        is_uncertain = False
        if cat_clean.lower() in ("uncertain", "unknown", "other", "none"):
            is_uncertain = True
            final_category = cls.UNCERTAIN_CATEGORY
        elif matched_canonical is None:
            raise AIValidationError(
                f"Model returned unsupported category '{cat_clean}'. Allowed: {categories_clean}",
                provider=provider.provider_name,
                model=model,
                correlation_id=correlation_id,
            )
        else:
            final_category = matched_canonical
            if confidence_val < confidence_threshold:
                is_uncertain = True

        from app.core.config import get_settings

        cost_calc = CostCalculator.calculate(
            model=model,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            usage_is_estimated=resp.usage.is_estimated,
            free_tier=get_settings().ai_free_tier_mode,
        )

        return AIClassificationResult(
            category=final_category,
            confidence=round(confidence_val, 4),
            reasoning=raw_reasoning.strip(),
            is_uncertain=is_uncertain,
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
