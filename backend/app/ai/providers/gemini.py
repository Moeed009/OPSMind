from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.ai.exceptions import (
    AIAuthenticationError,
    AIMalformedResponseError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.ai.providers.base import BaseAIProvider
from app.ai.schemas.ai_schemas import ProviderRequest, ProviderResponse, ProviderUsage

logger = logging.getLogger("opsmind.ai.providers.gemini")

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

SUPPORTED_GEMINI_MODELS: set[str] = {
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    
}

MODEL_API_ALIASES: dict[str, str] = {
    "gemini-1.5-flash": "gemini-2.5-flash",
    
}


class GeminiProvider(BaseAIProvider):
    """Official Google Gemini REST API provider."""

    provider_name = "gemini"

    def supports_model(self, model: str) -> bool:
        normalized = (model or "").strip().lower()
        return normalized in SUPPORTED_GEMINI_MODELS or normalized.startswith("gemini-")

    @staticmethod
    def resolve_api_model(model: str) -> str:
        normalized = (model or "gemini-2.5-flash").strip()
        return MODEL_API_ALIASES.get(normalized, normalized)

    async def _send_request(self, request: ProviderRequest) -> ProviderResponse:
        t0 = time.perf_counter()
        api_model = self.resolve_api_model(request.model)
        url = f"{GEMINI_API_BASE_URL}/{api_model}:generateContent"

        generation_config: dict[str, Any] = {
            "temperature": request.temperature,
            "maxOutputTokens": request.max_output_tokens,
        }
        if request.expect_json or request.response_schema:
            generation_config["responseMimeType"] = "application/json"

        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": request.prompt}]}],
            "generationConfig": generation_config,
        }
        if request.system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": request.system_instruction}],
            }

        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
            "X-Correlation-ID": request.correlation_id or "",
        }

        client = self.get_http_client()
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AITimeoutError(
                f"Gemini request timed out after {self.timeout_seconds}s.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
                cause=exc,
            ) from exc
        except httpx.RequestError as exc:
            raise AIProviderUnavailableError(
                f"Network error communicating with Gemini API: {exc}",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
                cause=exc,
            ) from exc

        latency_ms = max(1, int((time.perf_counter() - t0) * 1000))

        if resp.status_code in (401, 403):
            raise AIAuthenticationError(
                f"Gemini authentication failed with status {resp.status_code}.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )
        if resp.status_code == 429:
            retry_after_hdr = resp.headers.get("Retry-After")
            retry_after = (
                float(retry_after_hdr)
                if retry_after_hdr and retry_after_hdr.replace(".", "", 1).isdigit()
                else None
            )
            raise AIRateLimitError(
                "Gemini rate limit exceeded (HTTP 429).",
                retry_after_seconds=retry_after,
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )
        if resp.status_code >= 500 or resp.status_code == 404:
            raise AIProviderUnavailableError(
                f"Gemini server/endpoint unavailable (HTTP {resp.status_code}).",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )
        if resp.status_code >= 400:
            raise AIMalformedResponseError(
                f"Gemini rejected request with HTTP {resp.status_code}.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )

        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise AIMalformedResponseError(
                "Gemini returned non-JSON HTTP response body.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
                cause=exc,
            ) from exc

        if not isinstance(data, dict):
            raise AIMalformedResponseError(
                "Gemini response root must be a JSON object.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )

        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise AIMalformedResponseError(
                "Gemini response contained no candidates.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )

        first_candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        content_obj = first_candidate.get("content") if isinstance(first_candidate, dict) else {}
        parts = content_obj.get("parts") if isinstance(content_obj, dict) else []
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
            raise AIMalformedResponseError(
                "Gemini candidate contained no content parts.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )

        text_content = parts[0].get("text")
        if not isinstance(text_content, str) or not text_content.strip():
            raise AIMalformedResponseError(
                "Gemini candidate text content is empty.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )

        usage_meta = data.get("usageMetadata")
        if isinstance(usage_meta, dict) and (
            "promptTokenCount" in usage_meta or "candidatesTokenCount" in usage_meta
        ):
            prompt_tokens = int(usage_meta.get("promptTokenCount") or 0)
            completion_tokens = int(usage_meta.get("candidatesTokenCount") or 0)
            total_tokens = int(
                usage_meta.get("totalTokenCount") or (prompt_tokens + completion_tokens)
            )
            usage = ProviderUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                is_estimated=False,
            )
        else:
            est_prompt = self.estimate_tokens(
                (request.system_instruction or "") + "\n" + request.prompt
            )
            est_completion = self.estimate_tokens(text_content)
            usage = ProviderUsage(
                prompt_tokens=est_prompt,
                completion_tokens=est_completion,
                total_tokens=est_prompt + est_completion,
                is_estimated=True,
            )

        return ProviderResponse(
            content=text_content.strip(),
            provider=self.provider_name,
            model=request.model,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=first_candidate.get("finishReason"),
            correlation_id=request.correlation_id,
            raw_metadata={"api_model": api_model},
        )
