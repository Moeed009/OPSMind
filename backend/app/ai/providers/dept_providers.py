from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.ai.exceptions import (
    AIAuthenticationError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.ai.redaction import redact_text
from app.core.config import get_settings

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


@dataclass
class ProviderResult:
    text: str
    json_data: dict[str, Any] | None
    input_tokens: int
    output_tokens: int
    model: str
    latency_ms: int
    provider: str


class AIProviderInterface(Protocol):
    provider_name: str

    def is_configured(self) -> bool: ...

    async def generate(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        timeout: float = 30.0,
        model_override: str | None = None,
    ) -> ProviderResult: ...


def _parse_json_safe(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _provider_error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return "no details"
    err = body.get("error") if isinstance(body, dict) else None
    msg = err.get("message") if isinstance(err, dict) else err
    return redact_text(str(msg))[:200] if msg else "no details"


class GroqHttpProvider:
    provider_name = "groq"

    def __init__(self, *, api_key: str | None = None, default_model: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._api_key = settings.groq_api_key if api_key is None else api_key
        self._default_model = (settings.groq_model or settings.groq_pro_model) if default_model is None else default_model
        self._client = client

    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_key.strip())

    async def generate(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        timeout: float = 30.0,
        model_override: str | None = None,
    ) -> ProviderResult:
        if not self.is_configured():
            raise AIProviderUnavailableError("Groq provider is not configured", provider="groq")

        model = (model_override or self._default_model).strip()
        if not model:
            raise AIProviderUnavailableError("Groq model is not configured", provider="groq")

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

        t0 = time.perf_counter()
        try:
            if self._client is not None:
                resp = await self._client.post(GROQ_API_URL, json=payload, headers=headers, timeout=timeout)
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(GROQ_API_URL, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AITimeoutError("Groq request timed out", provider="groq", model=model, cause=exc) from exc
        except httpx.HTTPError as exc:
            raise AIProviderUnavailableError("Groq transport error", provider="groq", model=model, cause=exc) from exc

        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code in (401, 403):
            raise AIAuthenticationError("Groq authentication failed", provider="groq", model=model)
        if resp.status_code == 429:
            raise AIRateLimitError("Groq rate limit exceeded", provider="groq", model=model)
        if resp.status_code >= 500:
            raise AIProviderUnavailableError(f"Groq server error ({resp.status_code})", provider="groq", model=model)
        if resp.status_code >= 400:
            raise AIProviderUnavailableError(
                f"Groq rejected request ({resp.status_code}): {_provider_error_message(resp)}",
                provider="groq",
                model=model,
            )

        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        return ProviderResult(
            text=text,
            json_data=_parse_json_safe(text),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model=model,
            latency_ms=latency_ms,
            provider="groq",
        )


class GeminiHttpProvider:
    provider_name = "gemini"

    def __init__(self, *, api_key: str | None = None, default_model: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._api_key = settings.gemini_api_key if api_key is None else api_key
        self._default_model = (settings.gemini_model or settings.ai_fast_model) if default_model is None else default_model
        self._client = client

    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_key.strip())

    async def generate(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        timeout: float = 30.0,
        model_override: str | None = None,
    ) -> ProviderResult:
        if not self.is_configured():
            raise AIProviderUnavailableError("Gemini provider is not configured", provider="gemini")

        model = (model_override or self._default_model).strip()
        if not model:
            raise AIProviderUnavailableError("Gemini model is not configured", provider="gemini")

        url = f"{GEMINI_API_BASE}/{model}:generateContent"
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_content}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        headers = {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}

        t0 = time.perf_counter()
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=payload, headers=headers, timeout=timeout)
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AITimeoutError("Gemini request timed out", provider="gemini", model=model, cause=exc) from exc
        except httpx.HTTPError as exc:
            raise AIProviderUnavailableError("Gemini transport error", provider="gemini", model=model, cause=exc) from exc

        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code in (401, 403) or (
            resp.status_code == 400 and "API_KEY_INVALID" in resp.text
        ):
            raise AIAuthenticationError("Gemini authentication failed", provider="gemini", model=model)
        if resp.status_code == 429:
            raise AIRateLimitError("Gemini rate limit exceeded", provider="gemini", model=model)
        if resp.status_code >= 500:
            raise AIProviderUnavailableError(f"Gemini server error ({resp.status_code})", provider="gemini", model=model)
        if resp.status_code >= 400:
            raise AIProviderUnavailableError(
                f"Gemini rejected request ({resp.status_code}): {_provider_error_message(resp)}",
                provider="gemini",
                model=model,
            )

        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        meta = data.get("usageMetadata") or {}
        return ProviderResult(
            text=text,
            json_data=_parse_json_safe(text),
            input_tokens=int(meta.get("promptTokenCount") or 0),
            output_tokens=int(meta.get("candidatesTokenCount") or 0),
            model=model,
            latency_ms=latency_ms,
            provider="gemini",
        )


class FakeProvider:
    """Deterministic in-memory AI provider for unit and integration tests."""

    def __init__(
        self,
        provider_name: str = "groq",
        *,
        model: str = "fake-model",
        configured: bool = True,
        responses: list[str | Exception] | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.model = model
        self._configured = configured
        self._responses: list[str | Exception] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        return self._configured

    async def generate(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        timeout: float = 30.0,
        model_override: str | None = None,
    ) -> ProviderResult:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_content": user_content,
                "response_schema": response_schema,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "model_override": model_override,
            }
        )
        if not self._configured:
            raise AIProviderUnavailableError(f"{self.provider_name} is not configured", provider=self.provider_name)

        if self._responses:
            nxt = self._responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            raw = nxt
        else:
            raw = json.dumps({"text": "Simulated AI output", "label": "query", "score": 85, "confidence": 0.92})

        return ProviderResult(
            text=raw,
            json_data=_parse_json_safe(raw),
            input_tokens=24,
            output_tokens=16,
            model=model_override or self.model,
            latency_ms=12,
            provider=self.provider_name,
        )
