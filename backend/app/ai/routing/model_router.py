"""Dynamic, configuration-driven AI model router."""
from __future__ import annotations

from typing import Any

from app.ai.providers.registry import ProviderRegistry
from app.ai.schemas.ai_schemas import ModelTier, RoutingDecision
from app.core.config import get_settings

PRO_TASK_TYPES = {"resolution", "agent", "synthesis"}
FAST_TASK_TYPES = {"classify", "extract", "summarize", "generate"}


class ModelRouter:
    """Selects provider and model based on task, complexity, latency, cost, tenant policy, and availability."""

    @classmethod
    def route(
        cls,
        task_type: str,
        *,
        input_length: int = 0,
        complexity: str = "normal",
        priority: str = "normal",
        max_latency_ms: int | None = None,
        max_cost_usd: float | None = None,
        require_json: bool = False,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        tenant_config: dict[str, Any] | None = None,
        registry: ProviderRegistry | None = None,
    ) -> RoutingDecision:
        settings = get_settings()
        tenant_cfg = tenant_config or {}
        estimated_tokens = max(1, (input_length + 3) // 4) if input_length > 0 else 64

        provider_choice = (
            preferred_provider
            or tenant_cfg.get("preferred_provider")
            or settings.ai_default_provider
            or "gemini"
        ).lower().strip()

        if registry is not None and provider_choice != "offline":
            if not registry.is_available(provider_choice):
                avail = registry.available_providers()
                if avail:
                    alt = avail[0]
                    return cls._build_decision(
                        provider=alt,
                        tier=ModelTier.FAST,
                        reason=f"Fallback to available provider '{alt}' because '{provider_choice}' is unavailable.",
                        estimated_tokens=estimated_tokens,
                        settings=settings,
                        is_fallback=True,
                    )
                if settings.ai_allow_offline_fallback:
                    return RoutingDecision(
                        provider="offline",
                        model=ModelTier.OFFLINE.value,
                        tier=ModelTier.OFFLINE,
                        reason=f"Provider '{provider_choice}' is unconfigured/unavailable; routed to deterministic offline fallback.",
                        estimated_input_tokens=estimated_tokens,
                        max_output_tokens=1024,
                        is_fallback=True,
                    )

        explicit_model = preferred_model or tenant_cfg.get("preferred_model")
        if explicit_model:
            if explicit_model == ModelTier.OFFLINE.value or provider_choice == "offline":
                return RoutingDecision(
                    provider="offline",
                    model=ModelTier.OFFLINE.value,
                    tier=ModelTier.OFFLINE,
                    reason="Explicit offline deterministic model requested.",
                    estimated_input_tokens=estimated_tokens,
                    max_output_tokens=1024,
                )
            tier = ModelTier.PRO if "pro" in explicit_model.lower() or "sonnet" in explicit_model.lower() else ModelTier.FAST
            return RoutingDecision(
                provider=provider_choice,
                model=explicit_model,
                tier=tier,
                reason=f"Explicit model '{explicit_model}' selected via request/tenant configuration.",
                estimated_input_tokens=estimated_tokens,
                max_output_tokens=2048,
            )

        task_norm = (task_type or "classify").lower().strip()
        priority_norm = (priority or "normal").lower().strip()
        complexity_norm = (complexity or "normal").lower().strip()

        reasons: list[str] = []
        choose_pro = False

        if task_norm in PRO_TASK_TYPES:
            choose_pro = True
            reasons.append(f"task '{task_norm}' requires deep reasoning")
        if complexity_norm in ("high", "complex"):
            choose_pro = True
            reasons.append("high task complexity")
        if priority_norm in ("high", "critical", "urgent"):
            choose_pro = True
            reasons.append(f"priority='{priority_norm}'")
        if input_length > 4000 or estimated_tokens > 1000:
            choose_pro = True
            reasons.append(f"large context ({input_length} chars / ~{estimated_tokens} tokens)")

        # Cost and latency constraints can downgrade Pro to Fast unless tenant forces Pro
        if choose_pro and not tenant_cfg.get("force_pro"):
            if max_latency_ms is not None and max_latency_ms < 1500:
                choose_pro = False
                reasons.append(f"downgraded to fast tier due to strict latency budget ({max_latency_ms}ms)")
            elif max_cost_usd is not None and max_cost_usd < 0.0005:
                choose_pro = False
                reasons.append(f"downgraded to fast tier due to strict cost cap (${max_cost_usd})")
            elif tenant_cfg.get("plan") == "free" and tenant_cfg.get("cost_sensitive", False):
                choose_pro = False
                reasons.append("downgraded to fast tier per tenant cost-saving policy")

        if not reasons:
            reasons.append(f"standard fast tier for task '{task_norm}'")

        tier = ModelTier.PRO if choose_pro else ModelTier.FAST

        
        if (
            registry is not None
            and not preferred_provider
            and not tenant_cfg.get("preferred_provider")
            and registry.is_available("groq")
            and registry.is_available("gemini")
        ):
            if tier == ModelTier.FAST:
                provider_choice = "groq"
                reasons.append("routed fast tier to Groq LPU")
            else:
                provider_choice = "gemini"
                reasons.append("routed pro tier to Gemini Pro")

        return cls._build_decision(
            provider=provider_choice,
            tier=tier,
            reason="; ".join(reasons),
            estimated_tokens=estimated_tokens,
            settings=settings,
            is_fallback=False,
        )

    @classmethod
    def _build_decision(
        cls,
        *,
        provider: str,
        tier: ModelTier,
        reason: str,
        estimated_tokens: int,
        settings: Any,
        is_fallback: bool = False,
    ) -> RoutingDecision:
        if provider == "offline" or tier == ModelTier.OFFLINE:
            model_name = ModelTier.OFFLINE.value
        elif provider == "groq":
            model_name = settings.groq_pro_model if tier == ModelTier.PRO else settings.groq_fast_model
        else:
            model_name = settings.ai_pro_model if tier == ModelTier.PRO else settings.ai_fast_model

        return RoutingDecision(
            provider=provider,
            model=model_name,
            tier=tier,
            reason=reason,
            estimated_input_tokens=estimated_tokens,
            max_output_tokens=4096 if tier == ModelTier.PRO else 2048,
            is_fallback=is_fallback,
        )

    @classmethod
    def select_model(
        cls,
        task_type: str,
        input_length: int = 0,
        priority: str = "normal",
        **kwargs: Any,
    ) -> RoutingDecision:
        """Selects model and returns a RoutingDecision (which also compares equal to ModelTier)."""
        return cls.route(
            task_type=task_type,
            input_length=input_length,
            priority=priority,
            **kwargs,
        )
