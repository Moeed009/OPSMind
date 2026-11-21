"""Pydantic schemas and data contracts for the OpsMind AI integration layer."""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskType(StrEnum):
    CLASSIFY = "classify"
    EXTRACT = "extract"
    GENERATE = "generate"
    RESOLUTION = "resolution"
    AGENT = "agent"
    SUMMARIZE = "summarize"
    SYNTHESIS = "synthesis"


class ModelTier(StrEnum):
    FAST = "gemini-1.5-flash"
    PRO = "gemini-1.5-pro"
    OFFLINE = "deterministic-offline"


class ProviderUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    is_estimated: bool = False


class ProviderRequest(BaseModel):
    prompt: str = Field(min_length=1)
    system_instruction: str | None = None
    model: str
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=2048, ge=1, le=32768)
    expect_json: bool = False
    response_schema: dict[str, Any] | None = None
    correlation_id: str | None = None


class ProviderResponse(BaseModel):
    content: str
    provider: str
    model: str
    usage: ProviderUsage
    latency_ms: int = Field(default=0, ge=0)
    finish_reason: str | None = None
    correlation_id: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class RoutingDecision(BaseModel):
    provider: str
    model: str
    tier: ModelTier
    reason: str
    estimated_input_tokens: int = Field(default=0, ge=0)
    max_output_tokens: int = Field(default=2048, ge=1)
    is_fallback: bool = False

    @property
    def value(self) -> str:
        """Backward-compatible string representation matching ModelTier.value."""
        return self.tier.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ModelTier):
            return self.tier == other
        if isinstance(other, str):
            return self.model == other or self.tier.value == other
        return super().__eq__(other)


class ClassificationProviderPayload(BaseModel):
    category: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)


class AIClassificationResult(BaseModel):
    category: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    is_uncertain: bool = False
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = Decimal("0.000000")
    is_estimated_cost: bool = False
    model_used: str = ModelTier.FAST.value
    provider: str = "gemini"
    is_offline: bool = False
    correlation_id: str | None = None


class FieldSpec(BaseModel):
    name: str = Field(min_length=1)
    field_type: Literal["string", "number", "integer", "boolean", "array", "object"] = "string"
    required: bool = False
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field name cannot be empty")
        return cleaned


class AIExtractionResult(BaseModel):
    extracted: dict[str, Any]
    evidence: dict[str, str | None] = Field(default_factory=dict)
    missing_required_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = Decimal("0.000000")
    is_estimated_cost: bool = False
    model_used: str = ModelTier.FAST.value
    provider: str = "gemini"
    is_offline: bool = False
    correlation_id: str | None = None


class AIGenerationResult(BaseModel):
    content: str
    model: str
    format_type: Literal["email", "slack", "summary", "general"] = "general"
    is_draft: bool = True
    requires_approval: bool = False
    approval_reason: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = Decimal("0.000000")
    is_estimated_cost: bool = False
    provider: str = "gemini"
    is_offline: bool = False
    correlation_id: str | None = None

    @property
    def model_used(self) -> str:
        return self.model


class AISummarizationResult(BaseModel):
    summary: str
    key_points: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = Decimal("0.000000")
    is_estimated_cost: bool = False
    model_used: str = ModelTier.FAST.value
    provider: str = "gemini"
    is_offline: bool = False
    correlation_id: str | None = None
