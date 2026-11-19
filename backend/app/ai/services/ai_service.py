"""Provider-independent AIService orchestrating guardrails, routing, services, and usage tracking."""
from __future__ import annotations

import logging
import os
import uuid
from decimal import Decimal
from typing import Any, Literal

import httpx
from sqlalchemy.orm import Session

from app.ai.exceptions import (
    AIAuthenticationError,
    AIError,
    AIGuardrailBlockedError,
    AIMalformedResponseError,
    AIUsageLimitExceededError,
    AIValidationError,
)
from app.ai.guardrails.input_guard import InputGuardrail
from app.ai.guardrails.output_guard import OutputGuardrail
from app.ai.providers.registry import ProviderRegistry
from app.ai.routing.model_router import ModelRouter
from app.ai.schemas.ai_schemas import (
    AIClassificationResult,
    AIExtractionResult,
    AIGenerationResult,
    AISummarizationResult,
    FieldSpec,
    ModelTier,
)
from app.ai.services.classification import ClassificationService
from app.ai.services.extraction import ExtractionService
from app.ai.services.generation import GenerationService
from app.ai.services.summarization import SummarizationService
from app.ai.usage.usage_tracker import UsageTracker
from app.core.config import get_settings
from app.db.enums import GuardrailDecision

logger = logging.getLogger("opsmind.ai.service")


class AIService:
    """High-level AI integration facade independent of provider-specific HTTP transport."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        groq_api_key: str | None = None,
        registry: ProviderRegistry | None = None,
        allow_offline_fallback: bool | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = (
            api_key
            if api_key is not None
            else (os.getenv("GEMINI_API_KEY") or settings.gemini_api_key)
        )
        self.allow_offline_fallback = (
            allow_offline_fallback
            if allow_offline_fallback is not None
            else settings.ai_allow_offline_fallback
        )
        self.registry = registry or ProviderRegistry(
            gemini_api_key=api_key,
            groq_api_key=groq_api_key,
            http_client=http_client,
        )

    @property
    def is_live_configured(self) -> bool:
        return len(self.registry.available_providers()) > 0

    async def aclose(self) -> None:
        await self.registry.aclose_all()

    async def classify(
        self,
        text: str,
        categories: list[str],
        prompt: str | None = None,
        model: str | None = None,
        *,
        provider: str | None = None,
        tenant_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        db: Session | None = None,
        run_id: uuid.UUID | None = None,
        run_step_id: uuid.UUID | None = None,
        workflow_id: uuid.UUID | None = None,
        tenant_config: dict[str, Any] | None = None,
        allow_offline_fallback: bool | None = None,
        correlation_id: str | None = None,
    ) -> AIClassificationResult:
        corr_id = correlation_id or uuid.uuid4().hex[:16]
        guard_in = InputGuardrail.inspect_and_sanitize(
            text,
            db=db,
            tenant_id=tenant_id,
            run_id=run_id,
            run_step_id=run_step_id,
            correlation_id=corr_id,
            raise_on_block=True,
        )
        sanitized_text = guard_in.sanitized_text

        UsageTracker.check_budget(
            tenant_id=tenant_id,
            department_id=department_id,
            db=db,
            estimated_cost_usd=Decimal("0.000010"),
            correlation_id=corr_id,
        )

        decision = ModelRouter.route(
            "classify",
            input_length=len(sanitized_text),
            preferred_provider=provider,
            preferred_model=model,
            tenant_config=tenant_config,
            registry=self.registry,
        )
        can_fallback = (
            allow_offline_fallback
            if allow_offline_fallback is not None
            else self.allow_offline_fallback
        )

        if decision.tier == ModelTier.OFFLINE or decision.provider == "offline":
            if not can_fallback and model != ModelTier.OFFLINE.value:
                raise AIAuthenticationError(
                    f"Provider '{provider or 'gemini'}' is not configured and offline fallback is disabled.",
                    provider=provider or "gemini",
                    model=decision.model,
                    correlation_id=corr_id,
                )
            res = ClassificationService.classify_offline(
                text=sanitized_text,
                categories=categories,
                correlation_id=corr_id,
            )
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider="offline",
                model=res.model_used,
                task_type="classify",
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                latency_ms=res.latency_ms,
                status="succeeded",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
            )
            return res

        prov = self.registry.get(decision.provider)
        wrapped_text = InputGuardrail.wrap_untrusted_content(sanitized_text, "business_message")
        try:
            res = await ClassificationService.classify_with_provider(
                provider=prov,
                model=decision.model,
                text=wrapped_text,
                categories=categories,
                prompt=prompt,
                correlation_id=corr_id,
            )
            OutputGuardrail.inspect_output(
                res.reasoning,
                confidence=res.confidence,
                db=db,
                tenant_id=tenant_id,
                run_id=run_id,
                run_step_id=run_step_id,
                correlation_id=corr_id,
                raise_on_block=True,
            )
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=res.provider,
                model=res.model_used,
                task_type="classify",
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                latency_ms=res.latency_ms,
                status="succeeded",
                usage_is_estimated=res.is_estimated_cost,
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
            )
            return res
        except (
            AIAuthenticationError,
            AIMalformedResponseError,
            AIValidationError,
            AIGuardrailBlockedError,
            AIUsageLimitExceededError,
        ):
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=decision.provider,
                model=decision.model,
                task_type="classify",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                status="failed",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
                enforce_limit=False,
            )
            raise
        except AIError:
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=decision.provider,
                model=decision.model,
                task_type="classify",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                status="failed",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
                enforce_limit=False,
            )
            if can_fallback:
                return ClassificationService.classify_offline(
                    text=sanitized_text,
                    categories=categories,
                    correlation_id=corr_id,
                )
            raise

    async def extract(
        self,
        text: str,
        fields: list[str | FieldSpec | dict[str, Any]],
        prompt: str | None = None,
        model: str | None = None,
        *,
        provider: str | None = None,
        strict_required: bool = False,
        tenant_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        db: Session | None = None,
        run_id: uuid.UUID | None = None,
        run_step_id: uuid.UUID | None = None,
        workflow_id: uuid.UUID | None = None,
        tenant_config: dict[str, Any] | None = None,
        allow_offline_fallback: bool | None = None,
        correlation_id: str | None = None,
    ) -> AIExtractionResult:
        corr_id = correlation_id or uuid.uuid4().hex[:16]
        # Scan for prompt injection on input while preserving structured identifiers (invoices/order IDs)
        guard_in = InputGuardrail.inspect_and_sanitize(
            text,
            enable_presidio=False,
            db=db,
            tenant_id=tenant_id,
            run_id=run_id,
            run_step_id=run_step_id,
            correlation_id=corr_id,
            raise_on_block=True,
        )
        sanitized_text = guard_in.sanitized_text

        UsageTracker.check_budget(
            tenant_id=tenant_id,
            department_id=department_id,
            db=db,
            estimated_cost_usd=Decimal("0.000010"),
            correlation_id=corr_id,
        )

        decision = ModelRouter.route(
            "extract",
            input_length=len(sanitized_text),
            preferred_provider=provider,
            preferred_model=model,
            tenant_config=tenant_config,
            registry=self.registry,
        )
        can_fallback = (
            allow_offline_fallback
            if allow_offline_fallback is not None
            else self.allow_offline_fallback
        )

        if decision.tier == ModelTier.OFFLINE or decision.provider == "offline":
            if not can_fallback and model != ModelTier.OFFLINE.value:
                raise AIAuthenticationError(
                    f"Provider '{provider or 'gemini'}' is not configured and offline fallback is disabled.",
                    provider=provider or "gemini",
                    model=decision.model,
                    correlation_id=corr_id,
                )
            res = ExtractionService.extract_offline(
                text=sanitized_text,
                fields=fields,
                correlation_id=corr_id,
            )
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider="offline",
                model=res.model_used,
                task_type="extract",
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                latency_ms=res.latency_ms,
                status="succeeded",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
            )
            return res

        prov = self.registry.get(decision.provider)
        wrapped_text = InputGuardrail.wrap_untrusted_content(sanitized_text, "document_content")
        try:
            res = await ExtractionService.extract_with_provider(
                provider=prov,
                model=decision.model,
                text=wrapped_text,
                fields=fields,
                prompt=prompt,
                strict_required=strict_required,
                correlation_id=corr_id,
            )
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=res.provider,
                model=res.model_used,
                task_type="extract",
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                latency_ms=res.latency_ms,
                status="succeeded",
                usage_is_estimated=res.is_estimated_cost,
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
            )
            return res
        except (
            AIAuthenticationError,
            AIMalformedResponseError,
            AIValidationError,
            AIGuardrailBlockedError,
            AIUsageLimitExceededError,
        ):
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=decision.provider,
                model=decision.model,
                task_type="extract",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                status="failed",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
                enforce_limit=False,
            )
            raise
        except AIError:
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=decision.provider,
                model=decision.model,
                task_type="extract",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                status="failed",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
                enforce_limit=False,
            )
            if can_fallback:
                return ExtractionService.extract_offline(
                    text=sanitized_text,
                    fields=fields,
                    correlation_id=corr_id,
                )
            raise

    async def generate_resolution(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        model: str | None = None,
        *,
        provider: str | None = None,
        format_type: Literal["email", "slack", "summary", "general"] = "general",
        tenant_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        db: Session | None = None,
        run_id: uuid.UUID | None = None,
        run_step_id: uuid.UUID | None = None,
        workflow_id: uuid.UUID | None = None,
        tenant_config: dict[str, Any] | None = None,
        allow_offline_fallback: bool | None = None,
        correlation_id: str | None = None,
    ) -> AIGenerationResult:
        corr_id = correlation_id or uuid.uuid4().hex[:16]
        if not prompt or not prompt.strip():
            raise AIValidationError("Generation prompt cannot be empty.", correlation_id=corr_id)

        guard_in = InputGuardrail.inspect_and_sanitize(
            prompt,
            db=db,
            tenant_id=tenant_id,
            run_id=run_id,
            run_step_id=run_step_id,
            correlation_id=corr_id,
            raise_on_block=True,
        )
        sanitized_prompt = guard_in.sanitized_text
        sanitized_context = InputGuardrail.sanitize_context_dict(
            context,
            correlation_id=corr_id,
        )

        UsageTracker.check_budget(
            tenant_id=tenant_id,
            department_id=department_id,
            db=db,
            estimated_cost_usd=Decimal("0.000020"),
            correlation_id=corr_id,
        )

        decision = ModelRouter.route(
            "resolution",
            input_length=len(sanitized_prompt),
            preferred_provider=provider,
            preferred_model=model,
            tenant_config=tenant_config,
            registry=self.registry,
        )
        can_fallback = (
            allow_offline_fallback
            if allow_offline_fallback is not None
            else self.allow_offline_fallback
        )

        if decision.tier == ModelTier.OFFLINE or decision.provider == "offline":
            if not can_fallback and model != ModelTier.OFFLINE.value:
                raise AIAuthenticationError(
                    f"Provider '{provider or 'gemini'}' is not configured and offline fallback is disabled.",
                    provider=provider or "gemini",
                    model=decision.model,
                    correlation_id=corr_id,
                )
            res = GenerationService.generate_offline(
                prompt=sanitized_prompt,
                context=sanitized_context,
                format_type=format_type,
                correlation_id=corr_id,
            )
            guard_out = OutputGuardrail.inspect_output(
                res.content,
                confidence=res.confidence,
                db=db,
                tenant_id=tenant_id,
                run_id=run_id,
                run_step_id=run_step_id,
                correlation_id=corr_id,
                raise_on_block=True,
            )
            if guard_out.decision == GuardrailDecision.ESCALATED:
                res.requires_approval = True
                res.approval_reason = guard_out.reason
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider="offline",
                model=res.model,
                task_type="resolution",
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                latency_ms=res.latency_ms,
                status="succeeded",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
            )
            return res

        prov = self.registry.get(decision.provider)
        wrapped_prompt = InputGuardrail.wrap_untrusted_content(sanitized_prompt, "customer_request")
        try:
            res = await GenerationService.generate_with_provider(
                provider=prov,
                model=decision.model,
                prompt=wrapped_prompt,
                system_prompt=system_prompt,
                context=sanitized_context,
                format_type=format_type,
                correlation_id=corr_id,
            )
            guard_out = OutputGuardrail.inspect_output(
                res.content,
                confidence=res.confidence,
                db=db,
                tenant_id=tenant_id,
                run_id=run_id,
                run_step_id=run_step_id,
                correlation_id=corr_id,
                raise_on_block=True,
            )
            if guard_out.decision == GuardrailDecision.ESCALATED:
                res.requires_approval = True
                res.approval_reason = guard_out.reason

            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=res.provider,
                model=res.model,
                task_type="resolution",
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                latency_ms=res.latency_ms,
                status="succeeded",
                usage_is_estimated=res.is_estimated_cost,
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
            )
            return res
        except (
            AIAuthenticationError,
            AIMalformedResponseError,
            AIValidationError,
            AIGuardrailBlockedError,
            AIUsageLimitExceededError,
        ):
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=decision.provider,
                model=decision.model,
                task_type="resolution",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                status="failed",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
                enforce_limit=False,
            )
            raise
        except AIError:
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=decision.provider,
                model=decision.model,
                task_type="resolution",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                status="failed",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
                enforce_limit=False,
            )
            if can_fallback:
                return GenerationService.generate_offline(
                    prompt=sanitized_prompt,
                    context=sanitized_context,
                    format_type=format_type,
                    correlation_id=corr_id,
                )
            raise

    async def summarize(
        self,
        text: str,
        *,
        focus: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        tenant_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        db: Session | None = None,
        run_id: uuid.UUID | None = None,
        run_step_id: uuid.UUID | None = None,
        workflow_id: uuid.UUID | None = None,
        tenant_config: dict[str, Any] | None = None,
        allow_offline_fallback: bool | None = None,
        correlation_id: str | None = None,
    ) -> AISummarizationResult:
        corr_id = correlation_id or uuid.uuid4().hex[:16]
        if not text or not text.strip():
            raise AIValidationError("Cannot summarize empty text.", correlation_id=corr_id)

        guard_in = InputGuardrail.inspect_and_sanitize(
            text,
            db=db,
            tenant_id=tenant_id,
            run_id=run_id,
            run_step_id=run_step_id,
            correlation_id=corr_id,
            raise_on_block=True,
        )
        sanitized_text = guard_in.sanitized_text

        UsageTracker.check_budget(
            tenant_id=tenant_id,
            department_id=department_id,
            db=db,
            estimated_cost_usd=Decimal("0.000010"),
            correlation_id=corr_id,
        )

        decision = ModelRouter.route(
            "summarize",
            input_length=len(sanitized_text),
            preferred_provider=provider,
            preferred_model=model,
            tenant_config=tenant_config,
            registry=self.registry,
        )
        can_fallback = (
            allow_offline_fallback
            if allow_offline_fallback is not None
            else self.allow_offline_fallback
        )

        if decision.tier == ModelTier.OFFLINE or decision.provider == "offline":
            if not can_fallback and model != ModelTier.OFFLINE.value:
                raise AIAuthenticationError(
                    f"Provider '{provider or 'gemini'}' is not configured and offline fallback is disabled.",
                    provider=provider or "gemini",
                    model=decision.model,
                    correlation_id=corr_id,
                )
            res = SummarizationService.summarize_offline(
                text=sanitized_text,
                correlation_id=corr_id,
            )
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider="offline",
                model=res.model_used,
                task_type="summarize",
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                latency_ms=res.latency_ms,
                status="succeeded",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
            )
            return res

        prov = self.registry.get(decision.provider)
        wrapped_text = InputGuardrail.wrap_untrusted_content(sanitized_text, "conversation_record")
        try:
            res = await SummarizationService.summarize_with_provider(
                provider=prov,
                model=decision.model,
                text=wrapped_text,
                focus=focus,
                correlation_id=corr_id,
            )
            OutputGuardrail.inspect_output(
                res.summary,
                confidence=res.confidence,
                db=db,
                tenant_id=tenant_id,
                run_id=run_id,
                run_step_id=run_step_id,
                correlation_id=corr_id,
                raise_on_block=True,
            )
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=res.provider,
                model=res.model_used,
                task_type="summarize",
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                latency_ms=res.latency_ms,
                status="succeeded",
                usage_is_estimated=res.is_estimated_cost,
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
            )
            return res
        except (
            AIAuthenticationError,
            AIMalformedResponseError,
            AIValidationError,
            AIGuardrailBlockedError,
            AIUsageLimitExceededError,
        ):
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=decision.provider,
                model=decision.model,
                task_type="summarize",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                status="failed",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
                enforce_limit=False,
            )
            raise
        except AIError:
            UsageTracker.record_usage(
                tenant_id=tenant_id,
                provider=decision.provider,
                model=decision.model,
                task_type="summarize",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                status="failed",
                db=db,
                run_id=run_id,
                workflow_id=workflow_id,
                department_id=department_id,
                correlation_id=corr_id,
                enforce_limit=False,
            )
            if can_fallback:
                return SummarizationService.summarize_offline(
                    text=sanitized_text,
                    correlation_id=corr_id,
                )
            raise


ai_service = AIService()
