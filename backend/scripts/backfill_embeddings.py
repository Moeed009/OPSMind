"""Safe, auditable backfill script for `doc_chunks.embedding` (F-07b).

Default mode is `--dry-run` (read-only):
- Prints total `doc_chunks` with `embedding IS NULL` and a tenant-wise breakdown.
- Makes ZERO calls to Gemini and rolls back the transaction with 0 writes.

To apply backfill:
- Pass `--apply --confirm-count <N>` where `<N>` matches the exact count of `doc_chunks`
  with `embedding IS NULL`.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.doc_chunk import DocChunk
from app.db.models.document import Document
from app.services.embeddings import GeminiEmbeddingService


@dataclass(frozen=True)
class TenantMissingSummary:
    tenant_id: str
    slug: str
    total_chunks: int
    missing_embeddings: int


@dataclass(frozen=True)
class BackfillReport:
    total_chunks: int
    total_missing: int
    updated_chunks: int
    failed_chunks: int
    tenants: list[TenantMissingSummary]


def run_backfill(
    database_url: str,
    *,
    apply: bool = False,
    confirm_count: int | None = None,
    tenant_id: uuid.UUID | None = None,
    embedding_service: GeminiEmbeddingService | None = None,
    session: Session | None = None,
) -> BackfillReport:
    """Inspect or backfill `doc_chunks` rows where `embedding IS NULL`."""
    owns_engine = session is None
    engine = create_engine(database_url, pool_pre_ping=True) if owns_engine else None

    def _execute_in_session(db: Session) -> BackfillReport:
        where_tenant_sql = "WHERE c.tenant_id = :tid" if tenant_id is not None else ""
        params = {"tid": str(tenant_id)} if tenant_id is not None else {}

        rows = db.execute(
            text(
                f"""
                SELECT
                    t.id::text AS tenant_id,
                    t.slug AS slug,
                    COUNT(c.id) AS total_chunks,
                    COUNT(c.id) FILTER (WHERE c.embedding IS NULL) AS missing_embeddings
                FROM doc_chunks c
                JOIN tenants t ON t.id = c.tenant_id
                {where_tenant_sql}
                GROUP BY t.id, t.slug
                ORDER BY missing_embeddings DESC, t.slug ASC
                """
            ),
            params,
        ).all()

        summaries = [
            TenantMissingSummary(
                tenant_id=str(r.tenant_id),
                slug=str(r.slug),
                total_chunks=int(r.total_chunks),
                missing_embeddings=int(r.missing_embeddings),
            )
            for r in rows
        ]
        total_chunks = sum(s.total_chunks for s in summaries)
        total_missing = sum(s.missing_embeddings for s in summaries)

        print(f"=== EMBEDDING BACKFILL AUDIT (mode={'APPLY' if apply else 'DRY-RUN'}) ===")
        print(f"Total doc_chunks           : {total_chunks}")
        print(f"Chunks with embedding NULL : {total_missing}")
        if summaries:
            print("\nTenant-wise breakdown:")
            for s in summaries:
                print(
                    f"  {s.tenant_id} | {s.slug:26s} | missing={s.missing_embeddings:4d} / total={s.total_chunks:4d}"
                )
        else:
            print("\nNo doc_chunks rows found.")

        if not apply:
            print("\nDRY-RUN COMPLETE: 0 embeddings requested, 0 DB writes performed.")
            return BackfillReport(
                total_chunks=total_chunks,
                total_missing=total_missing,
                updated_chunks=0,
                failed_chunks=0,
                tenants=summaries,
            )

        if confirm_count != total_missing:
            raise RuntimeError(
                f"ABORT: --apply requires --confirm-count {total_missing} (got {confirm_count})."
            )

        if total_missing == 0:
            print("\nNothing to backfill (0 chunks missing embeddings).")
            return BackfillReport(
                total_chunks=total_chunks,
                total_missing=0,
                updated_chunks=0,
                failed_chunks=0,
                tenants=summaries,
            )

        svc = embedding_service or GeminiEmbeddingService()
        if not svc.is_configured():
            raise RuntimeError("ABORT: GEMINI_API_KEY is not configured; cannot run --apply.")

        stmt = (
            select(DocChunk, Document.title)
            .join(Document, Document.id == DocChunk.document_id)
            .where(DocChunk.embedding.is_(None))
            .order_by(DocChunk.tenant_id.asc(), DocChunk.document_id.asc(), DocChunk.chunk_index.asc())
        )
        if tenant_id is not None:
            stmt = stmt.where(DocChunk.tenant_id == tenant_id)

        targets = db.execute(stmt).all()
        texts = [chunk.content for chunk, _title in targets]
        vectors = svc.embed_batch(texts, task_type="RETRIEVAL_DOCUMENT")

        updated = 0
        failed = 0
        for (chunk, _title), vec in zip(targets, vectors, strict=True):
            if vec is not None:
                chunk.embedding = vec
                chunk.embedding_model = svc.model
                updated += 1
            else:
                failed += 1

        db.flush()
        print(f"\nAPPLY COMPLETE: updated={updated}, failed={failed}.")
        return BackfillReport(
            total_chunks=total_chunks,
            total_missing=total_missing,
            updated_chunks=updated,
            failed_chunks=failed,
            tenants=summaries,
        )

    if not owns_engine:
        assert session is not None
        return _execute_in_session(session)

    assert engine is not None
    try:
        with engine.connect() as conn, conn.begin() as tx:
            db = Session(bind=conn)
            report = _execute_in_session(db)
            if not apply:
                tx.rollback()
            else:
                tx.commit()
            return report
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit or backfill NULL embeddings in doc_chunks (default: --dry-run)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Read-only audit of NULL embeddings per tenant (default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Compute and persist embeddings for chunks where embedding IS NULL.",
    )
    parser.add_argument(
        "--confirm-count",
        type=int,
        default=None,
        help="Required with --apply: exact number of NULL-embedding chunks expected.",
    )
    parser.add_argument(
        "--tenant-id",
        type=str,
        default=None,
        help="Optional tenant UUID filter.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    db_url = (os.environ.get("DATABASE_URL") or settings.database_url).strip()
    if not db_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 2

    tid = uuid.UUID(args.tenant_id) if args.tenant_id else None
    try:
        run_backfill(
            db_url,
            apply=bool(args.apply),
            confirm_count=args.confirm_count,
            tenant_id=tid,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
