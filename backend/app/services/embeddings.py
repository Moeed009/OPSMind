"""Gemini embedding generation service (`gemini-embedding-001`, 384 dimensions) for F-07b hybrid retrieval.

Features:
- Uses Gemini REST API (`embedContent` / `batchEmbedContents`) with `outputDimensionality=384`
  and task types `RETRIEVAL_DOCUMENT` (for chunks) and `RETRIEVAL_QUERY` (for search queries).
- L2-normalizes every 384-dimensional vector so pgvector cosine distance (`<=>`) is exact.
- Redacts PII before any text leaves OpsMind via `PresidioPIIEngine.sanitize()` + `redact_text()`.
- Bounded timeout, exponential backoff retries on 429 / 5xx / transient transport errors,
  and batching (`batch_size=32`).
- Fail-open for ingestion/search resilience: if `GEMINI_API_KEY` is missing or the API fails
  after retries, logs a warning and returns `None` (never raises into document ingestion or search).
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Literal

import httpx

from app.ai.redaction import redact_text
from app.core.config import get_settings
from app.db.models.doc_chunk import EMBEDDING_DIMENSIONS
from app.workflows.presidio_engine import PresidioPIIEngine

logger = logging.getLogger("opsmind.services.embeddings")

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"
EmbeddingTaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]

# Module-level hook allowing tests to inject a custom/mocked httpx.Client without touching signatures
_default_http_client: httpx.Client | None = None


def set_default_embedding_http_client(client: httpx.Client | None) -> None:
    """Configure an optional default `httpx.Client` (used by unit/integration tests)."""
    global _default_http_client
    _default_http_client = client


def l2_normalize(vector: list[float]) -> list[float]:
    """L2-normalize a vector to unit length. Returns a zero vector if the input norm is 0."""
    norm_sq = sum(float(v) * float(v) for v in vector)
    if norm_sq <= 0.0:
        return [0.0 for _ in vector]
    norm = math.sqrt(norm_sq)
    return [float(v) / norm for v in vector]


def redact_for_embedding(raw_text: str, *, use_presidio: bool = True) -> str:
    """Redact PII and secrets before sending text to the external Gemini embedding API."""
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return ""
    settings = get_settings()
    if not settings.ai_redact_outbound_pii:
        return cleaned
    step1 = redact_text(cleaned)
    if use_presidio:
        try:
            scan = PresidioPIIEngine.sanitize(step1)
            return redact_text(scan.anonymized_text)
        except Exception as exc:
            logger.warning("Presidio redaction fallback during embedding sanitization: %s", exc)
    return step1


class GeminiEmbeddingService:
    """Synchronous Gemini embedding client with PII redaction, L2 normalization, retry/429 handling, and batching."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        batch_size: int | None = None,
        backoff_base_seconds: float = 0.25,
        http_client: httpx.Client | None = None,
        use_presidio: bool = True,
    ) -> None:
        settings = get_settings()
        self.api_key = (settings.gemini_api_key if api_key is None else api_key).strip()
        self.model = (settings.embedding_model if model is None else model).strip() or GEMINI_EMBEDDING_MODEL
        self.dimensions = int(settings.embedding_dimensions if dimensions is None else dimensions)
        self.timeout_seconds = float(
            settings.embedding_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        self.max_retries = max(0, int(settings.embedding_max_retries if max_retries is None else max_retries))
        self.batch_size = max(1, min(100, int(settings.embedding_batch_size if batch_size is None else batch_size)))
        self.backoff_base_seconds = max(0.0, float(backoff_base_seconds))
        self._client = http_client if http_client is not None else _default_http_client
        self.use_presidio = use_presidio

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _post_with_retry(self, url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_configured():
            return None

        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                if self._client is not None:
                    resp = self._client.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
                else:
                    with httpx.Client(timeout=self.timeout_seconds) as client:
                        resp = client.post(url, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < attempts - 1:
                    delay = self.backoff_base_seconds * (2**attempt)
                    if delay > 0:
                        time.sleep(delay)
                    continue
                logger.warning("Gemini embedding transport error after %d attempt(s): %s", attempts, exc)
                return None
            except Exception as exc:
                logger.warning("Unexpected error calling Gemini embedding endpoint: %s", exc)
                return None

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    return data if isinstance(data, dict) else None
                except Exception as exc:
                    logger.warning("Invalid JSON from Gemini embedding API: %s", exc)
                    return None

            # Retry on 429 rate-limit or 5xx transient server errors
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                retry_after_hdr = resp.headers.get("Retry-After")
                delay = self.backoff_base_seconds * (2**attempt)
                if retry_after_hdr:
                    try:
                        delay = max(delay, min(float(retry_after_hdr), 5.0))
                    except ValueError:
                        pass
                if delay > 0:
                    time.sleep(delay)
                continue

            logger.warning("Gemini embedding API returned status %d; falling back to None.", resp.status_code)
            return None

        return None

    def _validate_and_normalize(self, raw_values: Any) -> list[float] | None:
        if not isinstance(raw_values, list) or len(raw_values) != self.dimensions:
            logger.warning(
                "Gemini embedding dimension mismatch: expected %d, got %s",
                self.dimensions,
                len(raw_values) if isinstance(raw_values, list) else type(raw_values).__name__,
            )
            return None
        try:
            floats = [float(x) for x in raw_values]
        except (TypeError, ValueError):
            return None
        normed = l2_normalize(floats)
        if all(v == 0.0 for v in normed):
            return None
        return normed

    def embed_text(
        self,
        text: str,
        *,
        task_type: EmbeddingTaskType = "RETRIEVAL_DOCUMENT",
        title: str | None = None,
    ) -> list[float] | None:
        """Embed a single text string and return an L2-normalized 384-d vector, or `None` on failure."""
        if not self.is_configured():
            return None
        redacted = redact_for_embedding(text, use_presidio=self.use_presidio)
        if not redacted:
            return None

        model_path = self.model if self.model.startswith("models/") else f"models/{self.model}"
        url = f"{GEMINI_API_BASE}/{self.model}:embedContent"
        payload: dict[str, Any] = {
            "model": model_path,
            "content": {"parts": [{"text": redacted[:8000]}]},
            "taskType": task_type,
            "outputDimensionality": self.dimensions,
        }
        if title and task_type == "RETRIEVAL_DOCUMENT":
            payload["title"] = redact_for_embedding(title, use_presidio=self.use_presidio)[:300]

        data = self._post_with_retry(url, payload)
        if not data:
            return None
        emb_obj = data.get("embedding")
        if not isinstance(emb_obj, dict):
            return None
        return self._validate_and_normalize(emb_obj.get("values"))

    def embed_batch(
        self,
        texts: list[str],
        *,
        task_type: EmbeddingTaskType = "RETRIEVAL_DOCUMENT",
        title: str | None = None,
    ) -> list[list[float] | None]:
        """Embed a list of texts in batches using `batchEmbedContents`.

        Never raises: returns a list of length `len(texts)` where each item is either an
        L2-normalized 384-d `list[float]` or `None`.
        """
        if not texts:
            return []
        if not self.is_configured():
            return [None for _ in texts]

        results: list[list[float] | None] = [None for _ in texts]
        model_path = self.model if self.model.startswith("models/") else f"models/{self.model}"
        url = f"{GEMINI_API_BASE}/{self.model}:batchEmbedContents"
        redacted_title = (
            redact_for_embedding(title, use_presidio=self.use_presidio)[:300]
            if (title and task_type == "RETRIEVAL_DOCUMENT")
            else None
        )

        # Prepare redacted texts upfront and track non-empty indices
        indexed_redacted: list[tuple[int, str]] = []
        for idx, raw in enumerate(texts):
            red = redact_for_embedding(raw, use_presidio=self.use_presidio)
            if red:
                indexed_redacted.append((idx, red[:8000]))

        if not indexed_redacted:
            return results

        for start in range(0, len(indexed_redacted), self.batch_size):
            batch_slice = indexed_redacted[start : start + self.batch_size]
            requests_payload: list[dict[str, Any]] = []
            for _orig_idx, red_text in batch_slice:
                req_item: dict[str, Any] = {
                    "model": model_path,
                    "content": {"parts": [{"text": red_text}]},
                    "taskType": task_type,
                    "outputDimensionality": self.dimensions,
                }
                if redacted_title:
                    req_item["title"] = redacted_title
                requests_payload.append(req_item)

            data = self._post_with_retry(url, {"requests": requests_payload})
            if not data:
                continue
            embeddings_list = data.get("embeddings")
            if not isinstance(embeddings_list, list) or len(embeddings_list) != len(batch_slice):
                logger.warning(
                    "Gemini batchEmbedContents count mismatch: expected %d, got %s",
                    len(batch_slice),
                    len(embeddings_list) if isinstance(embeddings_list, list) else None,
                )
                continue

            for (orig_idx, _), emb_obj in zip(batch_slice, embeddings_list, strict=True):
                if isinstance(emb_obj, dict):
                    results[orig_idx] = self._validate_and_normalize(emb_obj.get("values"))

        return results


def embed_document_chunks(
    chunks: list[str],
    *,
    title: str | None = None,
    service: GeminiEmbeddingService | None = None,
) -> tuple[list[list[float] | None], str | None]:
    """Convenience helper for document ingestion (`knowledge.py` & `knowledge_sync.py`).

    Returns `(vectors, model_name)`. If embeddings are unavailable or fail for a chunk,
    its entry in `vectors` is `None` and document ingestion still succeeds with `DocumentStatus.READY`.
    """
    if not chunks:
        return ([], None)
    svc = service or GeminiEmbeddingService(dimensions=EMBEDDING_DIMENSIONS)
    if not svc.is_configured():
        return ([None for _ in chunks], None)
    vectors = svc.embed_batch(chunks, task_type="RETRIEVAL_DOCUMENT", title=title)
    return (vectors, svc.model)


def embed_query_text(
    query: str,
    *,
    service: GeminiEmbeddingService | None = None,
) -> list[float] | None:
    """Convenience helper for search query embedding (`RETRIEVAL_QUERY`). Returns `None` on failure."""
    svc = service or GeminiEmbeddingService(dimensions=EMBEDDING_DIMENSIONS)
    if not svc.is_configured():
        return None
    return svc.embed_text(query, task_type="RETRIEVAL_QUERY")
