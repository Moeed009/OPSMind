"""Knowledge Hub ingestion & connector Sync Service (tenant + department scoped)."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.connectors import crypto
from app.connectors.base import TokenSet
from app.connectors.httpx_client import HttpxClient
from app.connectors.zoho_books.connector import ZohoBooksConnector
from app.core.config import get_settings
from app.db.enums import ConnectionStatus, DocumentSource, DocumentStatus
from app.db.models.audit_log import AuditLog
from app.db.models.connection import Connection
from app.db.models.doc_chunk import DocChunk
from app.db.models.document import Document


def chunk_text(text: str, max_chars: int = 1000) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text.strip()] if text.strip() else []
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}".strip() if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) <= max_chars:
                current = para
            else:
                for i in range(0, len(para), max_chars):
                    chunks.append(para[i : i + max_chars])
                current = ""
    if current:
        chunks.append(current)
    return chunks or ([text.strip()] if text.strip() else [])


def upsert_knowledge_document(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID | None,
    scope: str,
    title: str,
    content: str,
    source: DocumentSource,
    external_id: str | None = None,
    connection_id: uuid.UUID | None = None,
    source_url: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Document:
    clean_scope = "department_private" if (scope == "department_private" and department_id is not None) else "company_shared"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)

    doc: Document | None = None
    if connection_id is not None and external_id is not None:
        doc = db.scalar(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.connection_id == connection_id,
                Document.external_id == external_id,
            )
        )

    if doc is None:
        doc = Document(
            tenant_id=tenant_id,
            connection_id=connection_id,
            department_id=department_id,
            scope=clean_scope,
            source=source,
            external_id=external_id,
            title=title[:500],
            source_url=source_url,
            mime_type="text/plain",
            size_bytes=len(content.encode("utf-8")),
            content_hash=content_hash,
            status=DocumentStatus.READY,
            indexed_at=now,
            uploaded_by=actor_user_id,
        )
        db.add(doc)
        db.flush()
    else:
        if doc.content_hash == content_hash and doc.status == DocumentStatus.READY:
            return doc
        doc.title = title[:500]
        doc.department_id = department_id
        doc.scope = clean_scope
        doc.size_bytes = len(content.encode("utf-8"))
        doc.content_hash = content_hash
        doc.status = DocumentStatus.READY
        doc.indexed_at = now
        db.execute(delete(DocChunk).where(DocChunk.tenant_id == tenant_id, DocChunk.document_id == doc.id))
        db.flush()

    for idx, ch in enumerate(chunk_text(content)):
        db.add(
            DocChunk(
                tenant_id=tenant_id,
                document_id=doc.id,
                chunk_index=idx,
                content=ch,
                token_count=max(1, len(ch.split())),
                metadata_={"title": doc.title, "scope": clean_scope},
            )
        )
    db.flush()
    return doc


def _decrypt_tokens(conn: Connection) -> dict[str, Any]:
    if not conn.credentials_ciphertext or not conn.credentials_key_id:
        return {}
    settings = get_settings()
    keyring = crypto.KeyRing.from_env(
        {
            "CONNECTOR_ENCRYPTION_KEYS": settings.connector_encryption_keys,
            "CONNECTOR_ENCRYPTION_CURRENT_KEY_ID": settings.connector_encryption_current_key_id,
        }
    )
    raw = conn.credentials_ciphertext
    ct = raw if isinstance(raw, str) else bytes(raw).decode("ascii")
    return crypto.decrypt_json(ct, conn.credentials_key_id, tenant_id=conn.tenant_id, connection_id=conn.id, keyring=keyring)


def _default_fetch_records(conn: Connection) -> list[dict[str, Any]]:
    tokens = _decrypt_tokens(conn)
    access_token = tokens.get("access_token")
    if not access_token:
        return []

    records: list[dict[str, Any]] = []
    if conn.provider == "hubspot":
        headers = {"Authorization": f"Bearer {access_token}"}
        for obj_type in ("contacts", "tickets", "deals"):
            resp = httpx.get(f"https://api.hubapi.com/crm/v3/objects/{obj_type}", headers=headers, params={"limit": 50}, timeout=15.0)
            if resp.status_code == 200:
                for item in (resp.json().get("results") or []):
                    records.append(
                        {
                            "external_id": f"hubspot:{obj_type}:{item.get('id')}",
                            "title": f"HubSpot {obj_type[:-1].title()} {item.get('id')}",
                            "content": json.dumps(item.get("properties") or {}),
                        }
                    )
    elif conn.provider == "zoho_books":
        import asyncio

        settings = get_settings()
        zoho = ZohoBooksConnector(
            HttpxClient(),
            settings.zoho_client_id or "placeholder",
            settings.zoho_client_secret or "placeholder",
        )
        batch = asyncio.run(zoho.poll(TokenSet.from_dict(tokens), None))
        for ev in batch.events:
            bill = ev.get("payload") or {}
            records.append(
                {
                    "external_id": f"zoho_books:{bill.get('bill_id')}",
                    "title": f"Zoho Bill {bill.get('bill_number') or bill.get('bill_id')}",
                    "content": json.dumps(bill),
                }
            )
    elif conn.provider == "google_drive":
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = httpx.get(
            "https://www.googleapis.com/drive/v3/files",
            headers=headers,
            params={"pageSize": 25, "fields": "files(id,name,mimeType)"},
            timeout=15.0,
        )
        if resp.status_code == 200:
            for f in (resp.json().get("files") or []):
                records.append(
                    {
                        "external_id": f"gdrive:{f.get('id')}",
                        "title": str(f.get("name") or f.get("id")),
                        "content": f"Google Drive Document: {f.get('name')} ({f.get('mimeType')})",
                    }
                )
    return records


def sync_connector_to_knowledge_hub(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    record_fetcher: Callable[[Connection], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Sync CRM/Invoicing/Drive records from a department connection into Knowledge Hub."""
    conn = db.scalar(
        select(Connection).where(
            Connection.tenant_id == tenant_id,
            Connection.id == connection_id,
        )
    )
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")
    if conn.status != ConnectionStatus.HEALTHY:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connection must be healthy before syncing")

    fetcher = record_fetcher or _default_fetch_records
    records = fetcher(conn)
    scope = "department_private" if conn.department_id is not None else "company_shared"
    source_enum = (
        DocumentSource.GDRIVE
        if conn.provider == "google_drive"
        else DocumentSource.UPLOAD
    )

    synced = 0
    for rec in records:
        content = str(rec.get("content") or "").strip()
        if not content:
            continue
        upsert_knowledge_document(
            db,
            tenant_id=tenant_id,
            department_id=conn.department_id,
            scope=scope,
            title=str(rec.get("title") or rec.get("external_id") or "Synced Record"),
            content=content,
            source=source_enum,
            external_id=str(rec.get("external_id") or uuid.uuid4()),
            connection_id=conn.id,
            actor_user_id=actor_user_id,
        )
        synced += 1

    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action="knowledge.synced_connector",
            entity_type="connection",
            entity_id=str(conn.id),
            details={"provider": conn.provider, "department_id": str(conn.department_id) if conn.department_id else None, "synced_count": synced},
        )
    )
    db.flush()
    return {"connection_id": str(conn.id), "provider": conn.provider, "scope": scope, "synced_count": synced}
