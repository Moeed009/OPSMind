from __future__ import annotations

import json
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

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"

SUPPORTED_GROQ_MODELS: set[str] = {
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
}


class GroqProvider(BaseAIProvider):
    """Official Groq OpenAI-compatible Chat Completions API provider."""

    provider_name = "groq"

    def supports_model(self, model: str) -> bool:
        normalized = (model or "").strip().lower()
        return (
            normalized in SUPPORTED_GROQ_MODELS
            or normalized.startswith("llama-")
            or normalized.startswith("mixtral-")
            or normalized.startswith("gemma")
        )

    async def _send_request(self, request: ProviderRequest) -> ProviderResponse:
        t0 = time.perf_counter()
        messages: list[dict[str, str]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        messages.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.expect_json or request.response_schema:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Correlation-ID": request.correlation_id or "",
        }

        client = self.get_http_client()
        try:
            resp = await client.post(GROQ_CHAT_COMPLETIONS_URL, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AITimeoutError(
                f"Groq request timed out after {self.timeout_seconds}s.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
                cause=exc,
            ) from exc
        except httpx.RequestError as exc:
            raise AIProviderUnavailableError(
                f"Network error communicating with Groq API: {exc}",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
                cause=exc,
            ) from exc

        latency_ms = max(1, int((time.perf_counter() - t0) * 1000))

        if resp.status_code in (401, 403):
            raise AIAuthenticationError(
                f"Groq authentication failed with status {resp.status_code}.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )
        if resp.status_code == 429:
            retry_after_hdr = resp.headers.get("retry-after")
            retry_after = (
                float(retry_after_hdr)
                if retry_after_hdr and retry_after_hdr.replace(".", "", 1).isdigit()
                else None
            )
            raise AIRateLimitError(
                "Groq rate limit exceeded (HTTP 429).",
                retry_after_seconds=retry_after,
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )
        if resp.status_code >= 500 or resp.status_code == 404:
            raise AIProviderUnavailableError(
                f"Groq server/endpoint unavailable (HTTP {resp.status_code}).",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )
        if resp.status_code >= 400:
            raise AIMalformedResponseError(
                f"Groq rejected request with HTTP {resp.status_code}.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )

        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise AIMalformedResponseError(
                "Groq returned non-JSON HTTP response body.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
                cause=exc,
            ) from exc

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise AIMalformedResponseError(
                "Groq response contained no choices.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )

        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        msg = first_choice.get("message") if isinstance(first_choice, dict) else {}
        text_content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(text_content, str) or not text_content.strip():
            raise AIMalformedResponseError(
                "Groq choice message content is empty.",
                provider=self.provider_name,
                model=request.model,
                correlation_id=request.correlation_id,
            )

        usage_obj = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage_obj, dict) and (
            "prompt_tokens" in usage_obj or "completion_tokens" in usage_obj
        ):
            in_tok = int(usage_obj.get("prompt_tokens") or 0)
            out_tok = int(usage_obj.get("completion_tokens") or 0)
            tot_tok = int(usage_obj.get("total_tokens") or (in_tok + out_tok))
            usage = ProviderUsage(
                prompt_tokens=in_tok,
                completion_tokens=out_tok,
                total_tokens=tot_tok,
                is_estimated=False,
            )
        else:
            est_in = self.estimate_tokens(request.prompt)
            est_out = self.estimate_tokens(text_content)
            usage = ProviderUsage(
                prompt_tokens=est_in,
                completion_tokens=est_out,
                total_tokens=est_in + est_out,
                is_estimated=True,
            )

        return ProviderResponse(
            content=text_content.strip(),
            provider=self.provider_name,
            model=request.model,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=first_choice.get("finish_reason"),
            correlation_id=request.correlation_id,
        )
