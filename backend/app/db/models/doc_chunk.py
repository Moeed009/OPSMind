import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.fk import tenant_fk
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIMENSIONS = 384


class DocChunk(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "doc_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        tenant_fk("document_id", "documents", ondelete="CASCADE"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        Index("ix_doc_chunks_tenant_id_document_id", "tenant_id", "document_id"),
        Index("ix_doc_chunks_content_tsv", "content_tsv", postgresql_using="gin"),
        Index(
            "ix_doc_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(100))