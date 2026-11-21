"""Centralized Knowledge Hub chunking and PostgreSQL Full-Text Search (FTS) retrieval (F-07a)."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Text, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.enums import DocumentStatus
from app.db.models.doc_chunk import DocChunk
from app.db.models.document import Document

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: str
    title: str
    scope: str
    chunk_index: int
    content: str
    rank: float

    def to_dict(self, *, preview_chars: int = 600) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "scope": self.scope,
            "chunk_index": self.chunk_index,
            "content": self.content[:preview_chars],
            "rank": round(self.rank, 6),
        }


def _tail_overlap(text_block: str, overlap_chars: int) -> str:
    """Return up to `overlap_chars` from the end of `text_block`, snapped to a word boundary."""
    if overlap_chars <= 0 or not text_block:
        return ""
    if len(text_block) <= overlap_chars:
        return text_block.strip()
    raw = text_block[-overlap_chars:]
    # Avoid starting mid-word if there is whitespace inside the overlap window
    first_space = raw.find(" ")
    if 0 < first_space < len(raw) - 1:
        raw = raw[first_space + 1 :]
    return raw.strip()


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """Split an oversized paragraph on sentence boundaries, falling back to word boundaries."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
    if not sentences:
        return []
    parts: list[str] = []
    buf = ""
    for sent in sentences:
        if len(sent) > max_chars:
            if buf:
                parts.append(buf)
                buf = ""
            # Word-boundary fallback for a single very long sentence
            start = 0
            while start < len(sent):
                end = min(start + max_chars, len(sent))
                if end < len(sent):
                    ws = sent.rfind(" ", start, end)
                    if ws > start:
                        end = ws
                piece = sent[start:end].strip()
                if piece:
                    parts.append(piece)
                start = end
            continue
        candidate = f"{buf} {sent}".strip() if buf else sent
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            if buf:
                parts.append(buf)
            buf = sent
    if buf:
        parts.append(buf)
    return parts


def chunk_text(
    raw_text: str,
    *,
    max_chars: int = 1000,
    overlap_chars: int = 150,
) -> list[str]:
    """Paragraph- and sentence-boundary-aware text chunker with configurable overlap."""
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return []
    eff_max = max(1, int(max_chars))
    eff_overlap = max(0, min(int(overlap_chars), eff_max // 2))

    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    units: list[str] = []
    for para in paragraphs:
        if len(para) <= eff_max:
            units.append(para)
        else:
            units.extend(_split_long_paragraph(para, eff_max))

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
            continue
        sep = "\n\n"
        if len(current) + len(sep) + len(unit) <= eff_max:
            current = f"{current}{sep}{unit}"
        else:
            chunks.append(current)
            prefix = _tail_overlap(current, eff_overlap)
            if prefix and len(prefix) + len(sep) + len(unit) <= eff_max:
                current = f"{prefix}{sep}{unit}"
            else:
                current = unit
    if current:
        chunks.append(current)
    return chunks


def _build_or_tsquery_text(db: Session, query: str) -> str | None:
    """Use Postgres's english text-search parser to strip stop-words/punctuation, stem, and OR-join lexemes.

    Converts `plainto_tsquery('english', query)` (`'a' & 'b' & 'c'`) into an OR query (`'a' | 'b' | 'c'`)
    so long natural-language inputs (e.g. support emails) do not fail when one word is absent from KB docs,
    while `ts_rank_cd` ranks chunks with more matching lexemes higher.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return None
    # Guard against excessively long payloads while still covering full emails/documents
    bounded = cleaned[:4000]
    tsq_text = db.scalar(
        select(
            func.nullif(
                func.replace(
                    cast(func.plainto_tsquery("english", bounded), Text),
                    " & ",
                    " | ",
                ),
                "",
            )
        )
    )
    return str(tsq_text) if tsq_text else None


def search_knowledge_chunks(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID | None,
    query: str,
    top_k: int | None = None,
    include_company_shared: bool = True,
    min_rank: float | None = None,
) -> list[RetrievedChunk]:
    """Ranked PostgreSQL FTS retrieval over `doc_chunks.content_tsv` with strict tenant + department scoping."""
    settings = get_settings()
    eff_top_k = int(top_k if top_k is not None else settings.retrieval_default_top_k)
    eff_top_k = max(1, min(eff_top_k, settings.retrieval_max_top_k))
    eff_min_rank = float(settings.retrieval_min_rank if min_rank is None else min_rank)

    if department_id is None and not include_company_shared:
        return []

    tsq_text = _build_or_tsquery_text(db, query)
    if not tsq_text:
        return []

    tsq_expr = text("CAST(:tsq AS tsquery)").bindparams(tsq=tsq_text)
    rank_expr = func.ts_rank_cd(DocChunk.content_tsv, tsq_expr).label("fts_rank")

    if include_company_shared:
        scope_filter = (
            or_(Document.department_id == department_id, Document.scope == "company_shared")
            if department_id is not None
            else (Document.scope == "company_shared")
        )
    else:
        scope_filter = Document.department_id == department_id

    stmt = (
        select(DocChunk, Document, rank_expr)
        .join(Document, Document.id == DocChunk.document_id)
        .where(
            DocChunk.tenant_id == tenant_id,
            Document.tenant_id == tenant_id,
            Document.status == DocumentStatus.READY,
            scope_filter,
            DocChunk.content_tsv.op("@@")(tsq_expr),
            rank_expr > eff_min_rank,
        )
        .order_by(rank_expr.desc(), DocChunk.chunk_index.asc())
        .limit(eff_top_k)
    )

    rows = db.execute(stmt).all()
    return [
        RetrievedChunk(
            document_id=str(doc.id),
            title=doc.title,
            scope=doc.scope,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            rank=float(rank_val or 0.0),
        )
        for chunk, doc, rank_val in rows
    ]
