"""Centralized, configurable AI token cost calculator."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class ModelPricing:
    """Per-1M token pricing in USD."""

    input_per_million_usd: Decimal
    output_per_million_usd: Decimal


DEFAULT_MODEL_PRICING: dict[str, ModelPricing] = {
    "gemini-2.5-flash": ModelPricing(Decimal("0.15"), Decimal("0.60")),
    "gemini-2.5-pro": ModelPricing(Decimal("1.25"), Decimal("10.00")),
    "gemini-2.0-flash": ModelPricing(Decimal("0.10"), Decimal("0.40")),
    "gemini-1.5-flash": ModelPricing(Decimal("0.075"), Decimal("0.30")),
    "gemini-1.5-pro": ModelPricing(Decimal("1.25"), Decimal("5.00")),
    "llama-3.1-8b-instant": ModelPricing(Decimal("0.05"), Decimal("0.08")),
    "llama-3.3-70b-versatile": ModelPricing(Decimal("0.59"), Decimal("0.79")),
    "deterministic-offline": ModelPricing(Decimal("0.00"), Decimal("0.00")),
}


@dataclass(frozen=True)
class CostCalculation:
    cost_usd: Decimal
    is_estimated: bool
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CostCalculator:
    """Calculates actual or estimated USD cost from token counts and configurable model pricing."""

    _custom_pricing: dict[str, ModelPricing] = {}

    @classmethod
    def register_pricing(
        cls,
        model: str,
        input_per_million_usd: Decimal | str | float,
        output_per_million_usd: Decimal | str | float,
    ) -> None:
        cls._custom_pricing[model.lower().strip()] = ModelPricing(
            input_per_million_usd=Decimal(str(input_per_million_usd)),
            output_per_million_usd=Decimal(str(output_per_million_usd)),
        )

    @classmethod
    def get_pricing(cls, model: str) -> tuple[ModelPricing, bool]:
        key = (model or "").lower().strip()
        if key in cls._custom_pricing:
            return cls._custom_pricing[key], False
        if key in DEFAULT_MODEL_PRICING:
            return DEFAULT_MODEL_PRICING[key], False
        if "pro" in key or "sonnet" in key:
            return DEFAULT_MODEL_PRICING["gemini-2.5-pro"], True
        if "offline" in key:
            return DEFAULT_MODEL_PRICING["deterministic-offline"], False
        return DEFAULT_MODEL_PRICING["gemini-2.5-flash"], True

    @classmethod
    def calculate(
        cls,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        usage_is_estimated: bool = False,
        free_tier: bool = False,
    ) -> CostCalculation:
        p_tokens = max(0, int(prompt_tokens))
        c_tokens = max(0, int(completion_tokens))
        total = p_tokens + c_tokens

        if free_tier:
            return CostCalculation(
                cost_usd=Decimal("0.000000"),
                is_estimated=False,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=total,
            )

        pricing, unknown_model = cls.get_pricing(model)
        one_million = Decimal("1000000")

        input_cost = (Decimal(p_tokens) * pricing.input_per_million_usd) / one_million
        output_cost = (Decimal(c_tokens) * pricing.output_per_million_usd) / one_million
        raw_total = input_cost + output_cost
        quantized = raw_total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

        return CostCalculation(
            cost_usd=quantized,
            is_estimated=bool(usage_is_estimated or unknown_model),
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=total,
        )
