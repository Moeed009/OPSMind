from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import app.db.models  # noqa: F401
from app.db.base import Base

DIALECT = postgresql.dialect()


def ddl(table_name: str) -> str:
    table = Base.metadata.tables[table_name]
    parts = [str(CreateTable(table).compile(dialect=DIALECT))]
    parts += [str(CreateIndex(index).compile(dialect=DIALECT)) for index in table.indexes]
    return "\n".join(parts)


def test_doc_chunks_support_keyword_and_vector_search():
    statement = ddl("doc_chunks")
    assert "VECTOR(384)" in statement.upper()
    assert "GENERATED ALWAYS AS" in statement.upper()
    assert "USING gin" in statement
    assert "USING hnsw" in statement
    assert "vector_cosine_ops" in statement


def test_source_documents_are_deduplicated_per_connection():
    assert "uq_documents_tenant_id_connection_id_external_id" in ddl("documents")


def test_usage_records_are_idempotent_per_source_key():
    assert "uq_usage_records_tenant_id_source_key" in ddl("usage_records")


def test_all_section_8_tables_have_models():
    expected = {
        "tenants", "users", "memberships", "connections", "events", "workflows",
        "workflow_versions", "runs", "run_steps", "action_logs", "review_tasks",
        "documents", "doc_chunks", "eval_sets", "eval_results", "guardrail_events",
        "usage_records", "subscriptions", "api_keys", "audit_logs",
    }
    assert set(Base.metadata.tables) == expected