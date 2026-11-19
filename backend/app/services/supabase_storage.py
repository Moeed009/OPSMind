"""Supabase Storage client for private HR CV binary persistence."""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx

from app.core.config import get_settings


class StorageUploadError(RuntimeError):
    """Raised when uploading a CV binary to Supabase Storage fails."""


def build_cv_storage_path(
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    candidate_id: uuid.UUID,
    content_hash: str,
    filename: str,
) -> str:
    safe_name = Path(filename or "cv.pdf").name.replace(" ", "_")
    return f"{tenant_id}/{department_id}/{candidate_id}/{content_hash}_{safe_name}"


def upload_cv_to_supabase_storage(
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    candidate_id: uuid.UUID,
    content_hash: str,
    filename: str,
    mime_type: str,
    data: bytes,
    http_client: httpx.Client | None = None,
) -> str:
    """Upload a validated CV binary to the private Supabase Storage bucket and return its path."""
    settings = get_settings()
    base_url = (settings.supabase_url or "").rstrip("/")
    service_key = (settings.supabase_service_role_key or "").strip()
    bucket = (settings.supabase_cv_bucket or "hr-cvs").strip()

    if not base_url or not service_key:
        raise StorageUploadError("Supabase Storage credentials are not configured.")

    object_path = build_cv_storage_path(
        tenant_id=tenant_id,
        department_id=department_id,
        candidate_id=candidate_id,
        content_hash=content_hash,
        filename=filename,
    )
    url = f"{base_url}/storage/v1/object/{bucket}/{object_path}"
    headers = {
        "Authorization": f"Bearer {service_key}",
        "apikey": service_key,
        "Content-Type": mime_type,
        "x-upsert": "true",
    }

    try:
        if http_client is not None:
            resp = http_client.post(url, content=data, headers=headers, timeout=30.0)
        else:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, content=data, headers=headers)
    except httpx.HTTPError as exc:
        raise StorageUploadError(f"Supabase Storage transport error: {type(exc).__name__}") from exc

    if resp.status_code >= 400:
        raise StorageUploadError(f"Supabase Storage upload rejected ({resp.status_code}).")

    return object_path
