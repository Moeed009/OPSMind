"""F-07b Hybrid Retrieval tests: Gemini 384-d embeddings, PII redaction, retry/429, RRF fusion, pgvector scoping, and backfill dry-run.

All DB tests run on the isolated local `opsmind_test` DB inside rolled-back transactions
under the non-superuser `opsmind_app` role (`NOBYPASSRLS`) and use mocked HTTP transports
(zero real outbound network calls).
"""
from __future__ import annotations

import json
import math
import uuid

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ai.providers.dept_providers import FakeProvider
from app.ai.services.dept_ai_service import DepartmentAiService
from app.api.deps import TenantContext
from app.api.v1.knowledge import KnowledgeDocumentCreate, create_knowledge_document
from app.db.enums import (
    DepartmentStatus,
    DepartmentType,
    DocumentSource,
    DocumentStatus,
    Region,
    Role,
    RunStatus,
    TriggerSource,
    WorkflowApprovalStatus,
    WorkflowVersionStatus,
)
from app.db.models import (
    Department,
    DocChunk,
    Membership,
    Run,
    Tenant,
    User,
    Workflow,
    WorkflowApproval,
    WorkflowVersion,
)
from app.db.models.doc_chunk import EMBEDDING_DIMENSIONS
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.services.embeddings import (
    GEMINI_EMBEDDING_MODEL,
    GeminiEmbeddingService,
    l2_normalize,
    set_default_embedding_http_client,
)
from app.services.knowledge_sync import upsert_knowledge_document
from app.services.retrieval import search_knowledge_chunks
from app.workflows.dept_executor import execute_department_run
from app.workflows.template_seed import SEED_TEMPLATES
from scripts.backfill_embeddings import run_backfill


@pytest.fixture()
def app_role_db():
    """Rolled-back session connected as `opsmind_app` (NOBYPASSRLS)."""
    engine = get_engine()
    conn = engine.connect()
    outer_tx = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer_tx.rollback()
        conn.close()


def _make_basis_vector(axis: int, *, secondary_axis: int | None = None, secondary_weight: float = 0.0) -> list[float]:
    """Create a deterministic 384-d L2-normalized vector pointed along `axis` (with optional secondary component)."""
    raw = [0.0] * EMBEDDING_DIMENSIONS
    raw[axis % EMBEDDING_DIMENSIONS] = 1.0
    if secondary_axis is not None:
        raw[secondary_axis % EMBEDDING_DIMENSIONS] = secondary_weight
    return l2_normalize(raw)


def _seed_tenant_and_depts(db: Session):
    user = User(id=uuid.uuid4(), email=f"hybrid-{uuid.uuid4().hex[:8]}@acme.com")
    set_rls_context(db, user_id=user.id)
    db.add(user)
    db.flush()

    tenant = Tenant(
        id=uuid.uuid4(),
        name="Hybrid Retrieval Co",
        slug=f"hyb-{uuid.uuid4().hex[:8]}",
        region=Region.US,
    )
    set_rls_context(db, user_id=user.id, tenant_id=tenant.id)
    db.add(tenant)
    db.flush()

    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER))
    db.flush()

    support = Department(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Customer Support",
        code="support",
        department_type=DepartmentType.SUPPORT,
        status=DepartmentStatus.ACTIVE,
    )
    finance = Department(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Finance",
        code="finance",
        department_type=DepartmentType.FINANCE,
        status=DepartmentStatus.ACTIVE,
    )
    ops = Department(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Internal Operations",
        code="ops",
        department_type=DepartmentType.OPERATIONS,
        status=DepartmentStatus.ACTIVE,
    )
    db.add_all([support, finance, ops])
    db.flush()
    return user, tenant, support, finance, ops


def test_embedding_l2_normalization_and_task_types() -> None:
    captured_requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append({"url": str(request.url), "body": body})
        # Return an unnormalized 384-d vector (values 1..384)
        raw_vals = [float(i + 1) for i in range(EMBEDDING_DIMENSIONS)]
        if "batchEmbedContents" in str(request.url):
            reqs = body["requests"]
            return httpx.Response(
                200,
                json={"embeddings": [{"values": raw_vals} for _ in reqs]},
            )
        return httpx.Response(200, json={"embedding": {"values": raw_vals}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    svc = GeminiEmbeddingService(
        api_key="test-gemini-key",
        http_client=client,
        use_presidio=False,
    )

    doc_vecs = svc.embed_batch(["Company expense reimbursement policy."], task_type="RETRIEVAL_DOCUMENT", title="Expense SOP")
    assert len(doc_vecs) == 1
    assert doc_vecs[0] is not None
    assert len(doc_vecs[0]) == 384
    doc_norm = math.sqrt(sum(v * v for v in doc_vecs[0]))
    assert math.isclose(doc_norm, 1.0, rel_tol=1e-6)

    q_vec = svc.embed_text("How do I expense a flight?", task_type="RETRIEVAL_QUERY")
    assert q_vec is not None
    assert len(q_vec) == 384
    q_norm = math.sqrt(sum(v * v for v in q_vec))
    assert math.isclose(q_norm, 1.0, rel_tol=1e-6)

    # Confirm Gemini API taskType and outputDimensionality=384
    assert len(captured_requests) == 2
    batch_item = captured_requests[0]["body"]["requests"][0]
    assert batch_item["taskType"] == "RETRIEVAL_DOCUMENT"
    assert batch_item["outputDimensionality"] == 384
    assert batch_item["model"] == f"models/{GEMINI_EMBEDDING_MODEL}"

    single_body = captured_requests[1]["body"]
    assert single_body["taskType"] == "RETRIEVAL_QUERY"
    assert single_body["outputDimensionality"] == 384


def test_embedding_pii_redacted_before_leaving_opsmind() -> None:
    sent_payloads: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        text_sent = body["content"]["parts"][0]["text"]
        sent_payloads.append(text_sent)
        return httpx.Response(
            200,
            json={"embedding": {"values": _make_basis_vector(0)}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    svc = GeminiEmbeddingService(
        api_key="test-gemini-key",
        http_client=client,
        use_presidio=True,
    )

    sensitive = (
        "Contact jane.doe@company.org or call +1-415-555-0199 "
        "with token sk-liveSecretToken123456789 for wire details."
    )
    vec = svc.embed_text(sensitive, task_type="RETRIEVAL_DOCUMENT")
    assert vec is not None
    assert len(sent_payloads) == 1
    outbound_text = sent_payloads[0]
    assert "jane.doe@company.org" not in outbound_text
    assert "415-555-0199" not in outbound_text
    assert "sk-liveSecretToken123456789" not in outbound_text


def test_embedding_retry_on_429_and_graceful_fallback_when_key_missing() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, json={"error": {"message": "Rate limit"}})
        return httpx.Response(200, json={"embedding": {"values": _make_basis_vector(3)}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    svc = GeminiEmbeddingService(
        api_key="test-gemini-key",
        http_client=client,
        max_retries=3,
        backoff_base_seconds=0.01,
        use_presidio=False,
    )
    vec = svc.embed_text("Retry test query", task_type="RETRIEVAL_QUERY")
    assert vec is not None
    assert calls == 3

    # Missing key -> returns None without making any HTTP call
    unconfigured = GeminiEmbeddingService(api_key="", http_client=client)
    assert unconfigured.is_configured() is False
    assert unconfigured.embed_text("Any query") is None
    assert unconfigured.embed_batch(["Chunk A", "Chunk B"]) == [None, None]
    assert calls == 3


def test_semantic_match_via_vector_when_zero_keywords_overlap(app_role_db: Session) -> None:
    """A query with different wording ('automobile mileage stipends') matches a chunk about
    'personal vehicle fuel reimbursement' via pgvector cosine similarity even with 0 FTS matches."""
    _user, tenant, support, _finance, _ops = _seed_tenant_and_depts(app_role_db)

    semantic_vec = _make_basis_vector(10)
    unrelated_vec = _make_basis_vector(20)

    doc1 = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="Vehicle Fuel Reimbursement Guide",
        content="Field technicians driving a personal car for client visits receive fifty cents per mile.",
        source=DocumentSource.UPLOAD,
    )
    doc2 = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="Office Printer Maintenance",
        content="Replace the laser toner cartridge when printouts appear faded.",
        source=DocumentSource.UPLOAD,
    )

    chunk1 = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == doc1.id))
    chunk2 = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == doc2.id))
    assert chunk1 is not None and chunk2 is not None
    chunk1.embedding = semantic_vec
    chunk1.embedding_model = GEMINI_EMBEDDING_MODEL
    chunk2.embedding = unrelated_vec
    chunk2.embedding_model = GEMINI_EMBEDDING_MODEL
    app_role_db.flush()

    # Query shares ZERO stemmed words with doc1 ("automobile", "stipend", "commute")
    query_vec = _make_basis_vector(10, secondary_axis=11, secondary_weight=0.1)
    results = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        query="automobile stipend commute",
        query_embedding=query_vec,
        top_k=5,
    )
    assert len(results) == 1
    assert results[0].title == "Vehicle Fuel Reimbursement Guide"


def test_rrf_fusion_boosts_chunk_matching_both_fts_and_vector(app_role_db: Session) -> None:
    """A chunk present in both FTS and vector lists achieves a higher RRF score than single-list matches."""
    _user, tenant, support, _finance, _ops = _seed_tenant_and_depts(app_role_db)

    # Doc A: matches FTS only (no embedding / orthogonal embedding)
    doc_fts_only = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="FTS Only Chargeback Note",
        content="Chargeback dispute workflow: chargeback evidence and chargeback response deadlines.",
        source=DocumentSource.UPLOAD,
    )
    # Doc B: matches BOTH FTS ("chargeback dispute") AND vector (axis 42)
    doc_both = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="Hybrid Chargeback Playbook",
        content="Standard chargeback dispute resolution playbook for cardholder claims.",
        source=DocumentSource.UPLOAD,
    )
    # Doc C: matches Vector only (axis 42, different words)
    doc_vec_only = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="Vector Only Card Reversal SOP",
        content="Handling contested credit card ledger reversals with acquiring banks.",
        source=DocumentSource.UPLOAD,
    )

    ch_fts = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == doc_fts_only.id))
    ch_both = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == doc_both.id))
    ch_vec = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == doc_vec_only.id))
    assert ch_fts and ch_both and ch_vec

    ch_fts.embedding = _make_basis_vector(99)  # orthogonal to query (sim = 0.0 < 0.35)
    ch_both.embedding = _make_basis_vector(42, secondary_axis=43, secondary_weight=0.15)
    ch_vec.embedding = _make_basis_vector(42)  # rank #1 in vector list
    app_role_db.flush()

    results = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        query="chargeback dispute",
        query_embedding=_make_basis_vector(42),
        top_k=5,
    )
    assert len(results) == 3
    # Doc B appears in BOTH FTS (#2) and Vector (#2) -> RRF = 1/62 + 1/62 = 0.032258 > 1/61 = 0.016393
    assert results[0].title == "Hybrid Chargeback Playbook"
    assert results[0].rank > results[1].rank


def test_vector_similarity_threshold_and_unknown_question_zero_matches(app_role_db: Session) -> None:
    """Vector candidates below `retrieval_vector_min_similarity` (0.35) are rejected, so unknown
    questions like 'What is the Mars relocation allowance?' still return 0 matches and trigger Policy Q&A escalation."""
    _user, tenant, _support, _finance, ops = _seed_tenant_and_depts(app_role_db)

    doc = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=None,
        scope="company_shared",
        title="Domestic Travel Allowance Policy",
        content="Employees may claim a daily travel allowance of $75 for meals during domestic trips.",
        source=DocumentSource.UPLOAD,
    )
    ch = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == doc.id))
    assert ch is not None
    # Give the chunk an embedding with low cosine similarity (~0.20 < 0.35) to the Mars query embedding
    ch.embedding = _make_basis_vector(1, secondary_axis=2, secondary_weight=4.8)
    ch.embedding_model = GEMINI_EMBEDDING_MODEL
    app_role_db.flush()

    mars_query_vec = _make_basis_vector(1)
    # Cosine similarity between (1, 0, ...) and normalize((1, 4.8, ...)) is 1 / sqrt(1 + 23.04) = 0.204 < 0.35
    matches = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=ops.id,
        query="What is the Mars relocation allowance?",
        query_embedding=mars_query_vec,
        include_company_shared=True,
    )
    assert matches == []

    # Verify full Policy Q&A workflow still escalates with "I don't know" + WAITING_APPROVAL
    qa_tpl = next(t for t in SEED_TEMPLATES if t["key"] == "ops_policy_qa")
    wf = Workflow(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        department_id=ops.id,
        name="Policy Q&A",
        template_key="ops_policy_qa",
    )
    app_role_db.add(wf)
    app_role_db.flush()
    ver = WorkflowVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        workflow_id=wf.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition=qa_tpl["definition"],
    )
    app_role_db.add(ver)
    app_role_db.flush()
    run = Run(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        department_id=ops.id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        trigger_source=TriggerSource.MANUAL,
        status=RunStatus.QUEUED,
        trace_id=uuid.uuid4().hex,
        input={"question": "What is the Mars relocation allowance?"},
    )
    app_role_db.add(run)
    app_role_db.flush()

    fake_agent = FakeProvider(
        "groq",
        responses=[
            json.dumps(
                {
                    "text": "Hallucinated response",
                    "confidence": 0.9,
                    "has_reliable_source": True,
                    "sources": [],
                }
            )
        ],
    )
    svc = DepartmentAiService(providers={"groq": fake_agent, "gemini": FakeProvider("gemini")})
    finished = execute_department_run(app_role_db, run.id, ai_service=svc)
    assert finished.output["retrieve_1"]["match_count"] == 0
    assert "I don't know" in finished.output["agent_1"]["text"]
    assert finished.status == RunStatus.WAITING_APPROVAL
    approvals = app_role_db.scalars(
        select(WorkflowApproval).where(WorkflowApproval.run_id == run.id)
    ).all()
    assert len(approvals) == 1
    assert approvals[0].status == WorkflowApprovalStatus.PENDING


def test_vector_search_enforces_tenant_rls_and_department_scoping(app_role_db: Session) -> None:
    """Even if another tenant or another department's private chunk has an identical vector (cosine sim = 1.0),
    it must never be returned."""
    role_row = app_role_db.execute(
        text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).first()
    assert role_row is not None and role_row[0] == "opsmind_app" and role_row[1] is False and role_row[2] is False

    _u_a, tenant_a, support_a, finance_a, _ops_a = _seed_tenant_and_depts(app_role_db)
    target_vec = _make_basis_vector(77)

    # Finance private doc in Tenant A with exact matching vector
    fin_doc = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant_a.id,
        department_id=finance_a.id,
        scope="department_private",
        title="Finance Private Payroll Vector Doc",
        content="Executive bonus pool distribution formula.",
        source=DocumentSource.UPLOAD,
    )
    # Company shared doc in Tenant A with matching vector
    shared_doc = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant_a.id,
        department_id=None,
        scope="company_shared",
        title="Company Shared Holiday Calendar",
        content="Observed company holidays for all departments.",
        source=DocumentSource.UPLOAD,
    )
    for d in (fin_doc, shared_doc):
        ch = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == d.id))
        assert ch is not None
        ch.embedding = target_vec
        ch.embedding_model = GEMINI_EMBEDDING_MODEL
    app_role_db.flush()

    # Seed Tenant B with an identical vector
    _u_b, tenant_b, support_b, _fin_b, _ops_b = _seed_tenant_and_depts(app_role_db)
    b_doc = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant_b.id,
        department_id=support_b.id,
        scope="company_shared",
        title="Tenant B Secret Vector Doc",
        content="Tenant B confidential acquisition target.",
        source=DocumentSource.UPLOAD,
    )
    ch_b = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == b_doc.id))
    assert ch_b is not None
    ch_b.embedding = target_vec
    ch_b.embedding_model = GEMINI_EMBEDDING_MODEL
    app_role_db.flush()

    # Switch RLS context back to Tenant A and query as Support department
    set_rls_context(app_role_db, tenant_id=tenant_a.id)
    res_support = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant_a.id,
        department_id=support_a.id,
        query="compensation holiday schedule",
        query_embedding=target_vec,
        include_company_shared=True,
    )
    titles = [r.title for r in res_support]
    assert titles == ["Company Shared Holiday Calendar"]
    assert "Finance Private Payroll Vector Doc" not in titles
    assert "Tenant B Secret Vector Doc" not in titles

    # With include_company_shared=False, Support gets 0 results
    res_strict = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant_a.id,
        department_id=support_a.id,
        query="compensation holiday schedule",
        query_embedding=target_vec,
        include_company_shared=False,
    )
    assert res_strict == []


def test_ingestion_computes_embeddings_and_survives_embedding_api_failure(
    app_role_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1) When Gemini succeeds, `create_knowledge_document` and `upsert_knowledge_document` store 384-d embeddings + model.
    2) When Gemini returns 500, document ingestion STILL succeeds (`DocumentStatus.READY`) with `embedding=None` and FTS works."""
    user, tenant, support, _finance, _ops = _seed_tenant_and_depts(app_role_db)
    tc = TenantContext(user=user, tenant_id=tenant.id, role=Role.OWNER, db=app_role_db)

    # Case 1: Mock Gemini returning 200 with a valid 384-d vector
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key")

    ok_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "embeddings": [
                        {"values": _make_basis_vector(5)}
                        for _ in json.loads(req.content.decode("utf-8"))["requests"]
                    ]
                },
            )
        )
    )
    set_default_embedding_http_client(ok_client)
    try:
        created = create_knowledge_document(
            KnowledgeDocumentCreate(
                title="Embedded Upload Policy",
                content="All production deployments require two peer approvals before merge.",
                department_id=support.id,
                scope="department_private",
            ),
            tc=tc,
        )
        assert created.status == DocumentStatus.READY.value
        chunk_ok = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == created.id))
        assert chunk_ok is not None
        assert chunk_ok.embedding is not None
        assert len(chunk_ok.embedding) == 384
        assert chunk_ok.embedding_model == GEMINI_EMBEDDING_MODEL

        # Case 2: Mock Gemini returning 503 Service Unavailable -> ingestion must NOT fail
        fail_client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(503, json={"error": {"message": "Service unavailable"}})
            )
        )
        set_default_embedding_http_client(fail_client)

        sync_doc = upsert_knowledge_document(
            app_role_db,
            tenant_id=tenant.id,
            department_id=support.id,
            scope="department_private",
            title="Fallback FTS Incident Runbook",
            content="Database failover runbook: promote the standby replica and verify replication lag.",
            source=DocumentSource.UPLOAD,
        )
        assert sync_doc.status == DocumentStatus.READY
        chunk_null = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == sync_doc.id))
        assert chunk_null is not None
        assert chunk_null.embedding is None
        assert chunk_null.embedding_model is None

        # FTS retrieval still finds the chunk even though embedding is NULL and query embedding fails
        fts_hits = search_knowledge_chunks(
            app_role_db,
            tenant_id=tenant.id,
            department_id=support.id,
            query="standby replica failover runbook",
        )
        assert any(r.title == "Fallback FTS Incident Runbook" for r in fts_hits)
    finally:
        set_default_embedding_http_client(None)


def test_backfill_dry_run_makes_zero_writes_and_zero_gemini_calls(app_role_db: Session) -> None:
    """`run_backfill(..., apply=False)` accurately counts NULL embeddings without calling Gemini or mutating rows."""
    _user, tenant, support, _finance, _ops = _seed_tenant_and_depts(app_role_db)

    doc = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="Unembedded Legacy Doc",
        content="Legacy chunk one.\n\nLegacy chunk two.",
        source=DocumentSource.UPLOAD,
    )
    chunk_before = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == doc.id))
    assert chunk_before is not None and chunk_before.embedding is None

    api_called = False

    def forbidden_handler(_req: httpx.Request) -> httpx.Response:
        nonlocal api_called
        api_called = True
        return httpx.Response(500)

    spy_svc = GeminiEmbeddingService(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(forbidden_handler)),
    )

    report = run_backfill(
        "",
        apply=False,
        tenant_id=tenant.id,
        embedding_service=spy_svc,
        session=app_role_db,
    )
    assert api_called is False
    assert report.total_missing == 1
    assert report.updated_chunks == 0

    # Verify --apply without matching --confirm-count aborts safely
    with pytest.raises(RuntimeError, match="requires --confirm-count 1"):
        run_backfill(
            "",
            apply=True,
            confirm_count=999,
            tenant_id=tenant.id,
            embedding_service=spy_svc,
            session=app_role_db,
        )

    app_role_db.expire_all()
    chunk_after = app_role_db.scalar(select(DocChunk).where(DocChunk.document_id == doc.id))
    assert chunk_after is not None and chunk_after.embedding is None


def test_hermetic_network_guard_blocks_real_outbound_http() -> None:
    """Verify the transport-level network guard in `conftest.py` immediately raises on real outbound HTTP."""
    with httpx.Client() as client, pytest.raises(RuntimeError, match="HERMETIC NETWORK GUARD"):
        client.get("https://generativelanguage.googleapis.com/v1beta/models")
