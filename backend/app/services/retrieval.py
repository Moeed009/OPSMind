"""Centralized Knowledge Hub chunking and PostgreSQL Full-Text Search (FTS) retrieval (F-07a)."""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Integer, Text, cast, func, literal_column, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.enums import DocumentStatus
from app.db.models.doc_chunk import EMBEDDING_DIMENSIONS, DocChunk
from app.db.models.document import Document
from app.services.embeddings import GeminiEmbeddingService, embed_query_text, l2_normalize

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_SAFE_LEXEME_RE = re.compile(r"'([a-z0-9_]+)'")

# Focused queries (<= 8 unique stemmed lexemes) must match at least max(2, ceil(0.5 * N))
# lexemes when N >= 2, preventing single-word false positives (e.g. "allowance" in
# "What is the Mars relocation allowance?" matching an unrelated "travel allowance" chunk).
# Longer natural-language payloads (> 8 lexemes, e.g. full support emails) allow >= 1 match
# ranked by coverage-weighted ts_rank_cd.
_FOCUSED_QUERY_MAX_LEXEMES = 8
_FOCUSED_QUERY_MIN_RATIO = 0.5


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


def _extract_safe_lexemes(db: Session, query: str, *, max_lexemes: int | None = None) -> list[str]:
    """Extract deduplicated alphanumeric stemmed lexemes via Postgres `plainto_tsquery('english', :q)`.

    Because `plainto_tsquery` treats all tsquery operators (`&`, `|`, `!`, `:`, `'`, `"`, `(`, `)`)
    as plain punctuation and `_SAFE_LEXEME_RE` only accepts `^[a-z0-9_]+$` tokens, arbitrary user
    input can never trigger a `tsquery` syntax error.
    Caps output at `max_lexemes` (defaulting to `settings.retrieval_max_lexemes`, default 32)
    unique lexemes in order of appearance.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return []
    cap = max(1, int(max_lexemes if max_lexemes is not None else get_settings().retrieval_max_lexemes))
    bounded = cleaned[:4000]
    raw_tsq = db.scalar(select(cast(func.plainto_tsquery("english", bounded), Text)))
    if not raw_tsq:
        return []
    return list(dict.fromkeys(_SAFE_LEXEME_RE.findall(str(raw_tsq))))[:cap]


def _min_required_matched_lexemes(num_lexemes: int) -> int:
    """Compute minimum distinct query lexemes a chunk must match."""
    if num_lexemes <= 1:
        return num_lexemes
    if num_lexemes <= _FOCUSED_QUERY_MAX_LEXEMES:
        return max(2, math.ceil(num_lexemes * _FOCUSED_QUERY_MIN_RATIO))
    return 1


def _scope_clause(department_id: uuid.UUID | None, include_company_shared: bool):
    if include_company_shared:
        return (
            or_(Document.department_id == department_id, Document.scope == "company_shared")
            if department_id is not None
            else (Document.scope == "company_shared")
        )
    return Document.department_id == department_id


def _search_fts_candidates(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID | None,
    lexemes: list[str],
    candidate_limit: int,
    include_company_shared: bool,
    eff_min_rank: float,
) -> list[tuple[uuid.UUID, RetrievedChunk]]:
    """Run F-07a PostgreSQL FTS query with matched-lexeme guard and return `(chunk_id, RetrievedChunk)`."""
    if not lexemes:
        return []

    tsq_text = " | ".join(f"'{lex}'" for lex in lexemes)
    tsq_expr = text("CAST(:tsq AS tsquery)").bindparams(tsq=tsq_text)

    term_checks = [
        cast(
            DocChunk.content_tsv.op("@@")(
                literal_column(f"'''{lex}'''::tsquery")
            ),
            Integer,
        )
        for lex in lexemes
    ]
    matched_terms_expr = term_checks[0]
    for tc in term_checks[1:]:
        matched_terms_expr = matched_terms_expr + tc

    min_matched = _min_required_matched_lexemes(len(lexemes))
    rank_expr = (
        func.ts_rank_cd(DocChunk.content_tsv, tsq_expr) * matched_terms_expr / float(len(lexemes))
    ).label("fts_rank")
    scope_filter = _scope_clause(department_id, include_company_shared)

    stmt = (
        select(DocChunk, Document, rank_expr)
        .join(Document, Document.id == DocChunk.document_id)
        .where(
            DocChunk.tenant_id == tenant_id,
            Document.tenant_id == tenant_id,
            Document.status == DocumentStatus.READY,
            scope_filter,
            DocChunk.content_tsv.op("@@")(tsq_expr),
            matched_terms_expr >= min_matched,
            rank_expr > eff_min_rank,
        )
        .order_by(rank_expr.desc(), DocChunk.chunk_index.asc())
        .limit(candidate_limit)
    )

    rows = db.execute(stmt).all()
    return [
        (
            chunk.id,
            RetrievedChunk(
                document_id=str(doc.id),
                title=doc.title,
                scope=doc.scope,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                rank=float(rank_val or 0.0),
            ),
        )
        for chunk, doc, rank_val in rows
    ]


def _search_vector_candidates(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID | None,
    query_embedding: list[float],
    candidate_limit: int,
    include_company_shared: bool,
    min_similarity: float,
) -> list[tuple[uuid.UUID, RetrievedChunk]]:
    """Run pgvector cosine similarity search (`<=>` over HNSW index) with identical tenant/department scoping."""
    if len(query_embedding) != EMBEDDING_DIMENSIONS:
        return []

    normed_query = l2_normalize(query_embedding)
    if all(v == 0.0 for v in normed_query):
        return []

    distance_expr = DocChunk.embedding.cosine_distance(normed_query)
    similarity_expr = (1.0 - distance_expr).label("cosine_sim")
    max_distance = 1.0 - float(min_similarity)
    scope_filter = _scope_clause(department_id, include_company_shared)

    stmt = (
        select(DocChunk, Document, similarity_expr)
        .join(Document, Document.id == DocChunk.document_id)
        .where(
            DocChunk.tenant_id == tenant_id,
            Document.tenant_id == tenant_id,
            Document.status == DocumentStatus.READY,
            scope_filter,
            DocChunk.embedding.is_not(None),
            distance_expr <= max_distance,
        )
        .order_by(distance_expr.asc(), DocChunk.chunk_index.asc())
        .limit(candidate_limit)
    )

    rows = db.execute(stmt).all()
    return [
        (
            chunk.id,
            RetrievedChunk(
                document_id=str(doc.id),
                title=doc.title,
                scope=doc.scope,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                rank=float(sim_val or 0.0),
            ),
        )
        for chunk, doc, sim_val in rows
    ]


def fuse_rrf_candidates(
    fts_candidates: list[tuple[uuid.UUID, RetrievedChunk]],
    vector_candidates: list[tuple[uuid.UUID, RetrievedChunk]],
    *,
    top_k: int,
    rrf_k: int = 60,
) -> list[RetrievedChunk]:
    """Fuse ranked FTS and pgvector candidate lists using Reciprocal Rank Fusion (RRF).

    Score(d) = sum_{list in (fts, vec)} 1 / (rrf_k + rank_1_based(d)).
    Chunks appearing in both lists receive the sum of both reciprocal ranks and outrank
    single-list matches at comparable positions. Tie-breaking uses the best individual
    rank position, then original relevance/similarity score, then chunk_index.
    """
    if not fts_candidates and not vector_candidates:
        return []
    if not vector_candidates:
        return [item for _cid, item in fts_candidates[:top_k]]

    eff_k = max(1, int(rrf_k))
    scores: dict[uuid.UUID, float] = {}
    chunks_by_id: dict[uuid.UUID, RetrievedChunk] = {}
    best_pos: dict[uuid.UUID, int] = {}
    raw_signal: dict[uuid.UUID, float] = {}

    for pos, (cid, item) in enumerate(fts_candidates, start=1):
        scores[cid] = scores.get(cid, 0.0) + (1.0 / float(eff_k + pos))
        chunks_by_id[cid] = item
        best_pos[cid] = min(best_pos.get(cid, pos), pos)
        raw_signal[cid] = max(raw_signal.get(cid, 0.0), item.rank)

    for pos, (cid, item) in enumerate(vector_candidates, start=1):
        scores[cid] = scores.get(cid, 0.0) + (1.0 / float(eff_k + pos))
        if cid not in chunks_by_id:
            chunks_by_id[cid] = item
        best_pos[cid] = min(best_pos.get(cid, pos), pos)
        raw_signal[cid] = max(raw_signal.get(cid, 0.0), item.rank)

    ordered_ids = sorted(
        scores.keys(),
        key=lambda cid: (
            -scores[cid],
            best_pos[cid],
            -raw_signal[cid],
            chunks_by_id[cid].chunk_index,
        ),
    )

    fused: list[RetrievedChunk] = []
    for cid in ordered_ids[:top_k]:
        base = chunks_by_id[cid]
        fused.append(
            RetrievedChunk(
                document_id=base.document_id,
                title=base.title,
                scope=base.scope,
                chunk_index=base.chunk_index,
                content=base.content,
                rank=round(scores[cid], 6),
            )
        )
    return fused


def search_knowledge_chunks(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID | None,
    query: str,
    top_k: int | None = None,
    include_company_shared: bool = True,
    min_rank: float | None = None,
    min_vector_similarity: float | None = None,
    query_embedding: list[float] | None = None,
    embedding_service: GeminiEmbeddingService | None = None,
) -> list[RetrievedChunk]:
    """Hybrid FTS + pgvector cosine retrieval fused with Reciprocal Rank Fusion (F-07a + F-07b).

    - Always applies strict tenant + department scoping (`DocumentStatus.READY`) to both FTS and vector queries.
    - Preserves the F-07a minimum matched-lexeme guard on FTS candidates.
    - Applies `retrieval_vector_min_similarity` (default 0.35) to vector candidates.
    - Cleanly falls back to FTS-only when no query embedding is available (missing key or API error).
    """
    settings = get_settings()
    eff_top_k = int(top_k if top_k is not None else settings.retrieval_default_top_k)
    eff_top_k = max(1, min(eff_top_k, settings.retrieval_max_top_k))
    eff_min_rank = float(settings.retrieval_min_rank if min_rank is None else min_rank)
    eff_min_vec_sim = float(
        settings.retrieval_vector_min_similarity
        if min_vector_similarity is None
        else min_vector_similarity
    )
    candidate_limit = min(max(eff_top_k * 3, 15), settings.retrieval_max_top_k * 2)

    if department_id is None and not include_company_shared:
        return []

    lexemes = _extract_safe_lexemes(db, query, max_lexemes=settings.retrieval_max_lexemes)
    if not lexemes:
        return []

    fts_candidates = _search_fts_candidates(
        db,
        tenant_id=tenant_id,
        department_id=department_id,
        lexemes=lexemes,
        candidate_limit=candidate_limit,
        include_company_shared=include_company_shared,
        eff_min_rank=eff_min_rank,
    )

    resolved_embedding = query_embedding
    if resolved_embedding is None:
        resolved_embedding = embed_query_text(query, service=embedding_service)

    vector_candidates: list[tuple[uuid.UUID, RetrievedChunk]] = []
    if resolved_embedding is not None:
        vector_candidates = _search_vector_candidates(
            db,
            tenant_id=tenant_id,
            department_id=department_id,
            query_embedding=resolved_embedding,
            candidate_limit=candidate_limit,
            include_company_shared=include_company_shared,
            min_similarity=eff_min_vec_sim,
        )

    return fuse_rrf_candidates(
        fts_candidates,
        vector_candidates,
        top_k=eff_top_k,
        rrf_k=settings.retrieval_rrf_k,
    )
