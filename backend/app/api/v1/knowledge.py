"""Knowledge Hub API: Manage tenant- and department-scoped documents, chunks, and connector sync."""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import TenantContext
from app.api.rbac import require_permission
from app.core.permissions import Permission
from app.db.enums import DocumentSource, DocumentStatus
from app.db.models.audit_log import AuditLog
from app.db.models.connection import Connection
from app.db.models.doc_chunk import DocChunk
from app.db.models.document import Document
from app.services.embeddings import embed_document_chunks
from app.services.retrieval import chunk_text

_chunk_text = chunk_text

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(min_length=2, max_length=500)
    content: str = Field(min_length=10, max_length=100_000)
    source_url: str | None = None
    department_id: uuid.UUID | None = None
    scope: Literal["company_shared", "department_private"] = "company_shared"


class KnowledgeDocumentOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    department_id: uuid.UUID | None = None
    scope: str = "company_shared"
    title: str
    source: str
    status: str
    source_url: str | None = None
    size_bytes: int | None = None
    chunk_count: int = 0
    preview: str = ""
    indexed_at: datetime | None = None
    created_at: datetime


@router.get("", response_model=list[KnowledgeDocumentOut])
def list_knowledge_documents(
    tc: Annotated[TenantContext, Depends(require_permission(Permission.KNOWLEDGE_READ))],
    department_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[KnowledgeDocumentOut]:
    from app.services import department as department_service

    stmt = select(Document).where(Document.tenant_id == tc.tenant_id)
    if department_id is not None:
        department_service.check_department_access(
            tc.db,
            tenant_id=tc.tenant_id,
            user_id=tc.user.id,
            role=tc.role,
            department_id=department_id,
            require_write=False,
        )
        stmt = stmt.where(
            or_(Document.department_id == department_id, Document.scope == "company_shared")
        )
    elif not department_service.is_company_admin(tc.role):
        assigned_ids = department_service.get_assigned_department_ids(
            tc.db, tc.tenant_id, tc.user.id
        )
        if assigned_ids:
            stmt = stmt.where(
                or_(
                    Document.scope == "company_shared",
                    Document.department_id.in_(assigned_ids),
                )
            )
        else:
            stmt = stmt.where(Document.scope == "company_shared")
    docs = tc.db.scalars(stmt.order_by(Document.created_at.desc())).all()

    results: list[KnowledgeDocumentOut] = []
    for doc in docs:
        chunk_count = tc.db.scalar(
            select(func.count(DocChunk.id)).where(
                DocChunk.tenant_id == tc.tenant_id,
                DocChunk.document_id == doc.id,
            )
        ) or 0
        first_chunk = tc.db.scalar(
            select(DocChunk.content)
            .where(DocChunk.tenant_id == tc.tenant_id, DocChunk.document_id == doc.id)
            .order_by(DocChunk.chunk_index.asc())
            .limit(1)
        )
        results.append(
            KnowledgeDocumentOut(
                id=doc.id,
                tenant_id=doc.tenant_id,
                department_id=doc.department_id,
                scope=doc.scope,
                title=doc.title,
                source=doc.source.value,
                status=doc.status.value,
                source_url=doc.source_url,
                size_bytes=doc.size_bytes,
                chunk_count=int(chunk_count),
                preview=(first_chunk or "")[:240],
                indexed_at=doc.indexed_at,
                created_at=doc.created_at,
            )
        )
    return results


@router.post("", response_model=KnowledgeDocumentOut, status_code=status.HTTP_201_CREATED)
def create_knowledge_document(
    payload: KnowledgeDocumentCreate,
    tc: Annotated[TenantContext, Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
) -> KnowledgeDocumentOut:
    clean_title = payload.title.strip()
    clean_content = payload.content.strip()
    encoded = clean_content.encode("utf-8")
    now = datetime.now(UTC)

    if payload.scope == "department_private" and payload.department_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "department_id is required when scope is department_private.",
        )
    if payload.department_id is not None:
        from app.services import department as department_service

        department_service.check_department_access(
            tc.db,
            tenant_id=tc.tenant_id,
            user_id=tc.user.id,
            role=tc.role,
            department_id=payload.department_id,
            require_write=True,
        )

    doc = Document(
        tenant_id=tc.tenant_id,
        department_id=payload.department_id,
        scope=payload.scope,
        source=DocumentSource.UPLOAD,
        title=clean_title,
        source_url=payload.source_url.strip() if payload.source_url else None,
        mime_type="text/plain",
        size_bytes=len(encoded),
        content_hash=hashlib.sha256(encoded).hexdigest(),
        status=DocumentStatus.READY,
        indexed_at=now,
        uploaded_by=tc.user.id,
    )
    tc.db.add(doc)
    tc.db.flush()

    chunks = chunk_text(clean_content)
    vectors, emb_model = embed_document_chunks(chunks, title=clean_title)
    for idx, chunk_str in enumerate(chunks):
        vec = vectors[idx] if idx < len(vectors) else None
        tc.db.add(
            DocChunk(
                tenant_id=tc.tenant_id,
                document_id=doc.id,
                chunk_index=idx,
                content=chunk_str,
                token_count=max(1, len(chunk_str.split())),
                metadata_={"title": clean_title, "scope": payload.scope},
                embedding=vec,
                embedding_model=emb_model if vec is not None else None,
            )
        )

    tc.db.add(
        AuditLog(
            tenant_id=tc.tenant_id,
            actor_user_id=tc.user.id,
            action="document.created",
            entity_type="document",
            entity_id=str(doc.id),
            details={"title": clean_title, "chunks": len(chunks), "scope": payload.scope},
        )
    )
    tc.db.flush()

    return KnowledgeDocumentOut(
        id=doc.id,
        tenant_id=doc.tenant_id,
        department_id=doc.department_id,
        scope=doc.scope,
        title=doc.title,
        source=doc.source.value,
        status=doc.status.value,
        source_url=doc.source_url,
        size_bytes=doc.size_bytes,
        chunk_count=len(chunks),
        preview=chunks[0][:240] if chunks else "",
        indexed_at=doc.indexed_at,
        created_at=doc.created_at,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_document(
    document_id: uuid.UUID,
    tc: Annotated[TenantContext, Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
) -> None:
    from app.services import department as department_service

    doc = tc.db.scalar(
        select(Document).where(Document.tenant_id == tc.tenant_id, Document.id == document_id)
    )
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge document not found")

    if doc.scope == "company_shared" or doc.department_id is None:
        if not department_service.is_company_admin(tc.role):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only company owners or admins can delete company_shared documents.",
            )
    else:
        department_service.check_department_access(
            tc.db,
            tenant_id=tc.tenant_id,
            user_id=tc.user.id,
            role=tc.role,
            department_id=doc.department_id,
            require_write=True,
        )

    title = doc.title
    tc.db.delete(doc)
    tc.db.add(
        AuditLog(
            tenant_id=tc.tenant_id,
            actor_user_id=tc.user.id,
            action="document.deleted",
            entity_type="document",
            entity_id=str(document_id),
            details={"title": title},
        )
    )
    tc.db.flush()


@router.post("/sync/{connection_id}")
def sync_connection_knowledge(
    connection_id: uuid.UUID,
    tc: Annotated[TenantContext, Depends(require_permission(Permission.KNOWLEDGE_WRITE))],
) -> dict[str, Any]:
    from app.services import department as department_service
    from app.services.knowledge_sync import sync_connector_to_knowledge_hub

    conn = tc.db.scalar(
        select(Connection).where(Connection.tenant_id == tc.tenant_id, Connection.id == connection_id)
    )
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")
    if conn.department_id is not None:
        department_service.check_department_access(
            tc.db,
            tenant_id=tc.tenant_id,
            user_id=tc.user.id,
            role=tc.role,
            department_id=conn.department_id,
            require_write=True,
        )
    return sync_connector_to_knowledge_hub(
        tc.db,
        tenant_id=tc.tenant_id,
        connection_id=connection_id,
        actor_user_id=tc.user.id,
    )
