"""F-07a PostgreSQL Full-Text Search (FTS) retrieval and shared chunker tests.

All DB tests run on the isolated local `opsmind_test` DB inside rolled-back transactions
under the non-superuser `opsmind_app` role (where PostgreSQL RLS is genuinely enforced).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ai.providers.dept_providers import FakeProvider
from app.ai.services.dept_ai_service import DepartmentAiService
from app.core.config import get_settings
from app.db.enums import (
    DepartmentStatus,
    DepartmentType,
    DocumentSource,
    DocumentStatus,
    Region,
    RunStatus,
    TriggerSource,
    WorkflowApprovalStatus,
    WorkflowVersionStatus,
)
from app.db.models import (
    Department,
    DocChunk,
    Document,
    Run,
    Tenant,
    Workflow,
    WorkflowApproval,
    WorkflowVersion,
)
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.services.knowledge_sync import upsert_knowledge_document
from app.services.retrieval import _extract_safe_lexemes, chunk_text, search_knowledge_chunks
from app.workflows.dept_executor import execute_department_run
from app.workflows.template_seed import SEED_TEMPLATES


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


def _seed_tenant_and_depts(db: Session):
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Retrieval Test Co",
        slug=f"ret-{uuid.uuid4().hex[:8]}",
        region=Region.US,
    )
    set_rls_context(db, tenant_id=tenant.id)
    db.add(tenant)
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
        code="operations",
        department_type=DepartmentType.OPERATIONS,
        status=DepartmentStatus.ACTIVE,
    )
    db.add_all([support, finance, ops])
    db.flush()
    return tenant, support, finance, ops


def test_chunk_text_overlap_and_boundary_awareness() -> None:
    p1 = "First paragraph explains annual paid time off. Employees accrue twenty days every calendar year."
    p2 = "Second paragraph explains remote work equipment stipends. Every employee receives five hundred dollars."
    p3 = "Third paragraph explains expense reimbursement deadlines. Submit receipts within thirty days."
    combined = f"{p1}\n\n{p2}\n\n{p3}"

    chunks = chunk_text(combined, max_chars=160, overlap_chars=40)
    assert len(chunks) >= 2
    assert all(len(c) <= 160 for c in chunks)
    # Consecutive chunk carries overlap from tail of previous chunk
    assert "calendar year." in chunks[1] or "dollars." in chunks[-1]
    assert chunk_text("   ") == []


def test_fts_stop_words_and_unknown_question_returns_zero_matches(app_role_db: Session) -> None:
    tenant, _support, _finance, ops = _seed_tenant_and_depts(app_role_db)

    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=None,
        scope="company_shared",
        title="PTO Policy",
        content="Full-time employees receive 20 annual PTO days per year. Unused days roll over up to 5 days.",
        source=DocumentSource.UPLOAD,
    )
    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=None,
        scope="company_shared",
        title="Remote Work Stipend",
        content="Employees working remotely receive a one-time $500 home office equipment stipend.",
        source=DocumentSource.UPLOAD,
    )

    # Previously ILIKE matched "is"/"the" on every English doc; FTS must return 0 matches
    unknown = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=ops.id,
        query="What is the Mars relocation allowance?",
        include_company_shared=True,
    )
    assert unknown == []

    # Stop-word-only and empty queries return 0 matches cleanly
    assert (
        search_knowledge_chunks(
            app_role_db,
            tenant_id=tenant.id,
            department_id=ops.id,
            query="what is the of to in?",
        )
        == []
    )
    assert (
        search_knowledge_chunks(
            app_role_db,
            tenant_id=tenant.id,
            department_id=ops.id,
            query="   ",
        )
        == []
    )


def test_fts_punctuation_and_english_stemming(app_role_db: Session) -> None:
    tenant, support, _finance, _ops = _seed_tenant_and_depts(app_role_db)

    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="Refund Guidelines",
        content="Customers requesting a refund for an order within 30 days are eligible for full reimbursement.",
        source=DocumentSource.UPLOAD,
    )
    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=None,
        scope="company_shared",
        title="Relocation Allowance Policy",
        content="Approved domestic moves include a relocation allowance up to $3,000.",
        source=DocumentSource.UPLOAD,
    )

    # Trailing punctuation '?' stripped + stemmed
    m_allow = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        query="What is the relocation allowance?",
    )
    assert len(m_allow) == 1
    assert m_allow[0].title == "Relocation Allowance Policy"

    # Plural/gerund stemming ("refunds", "refunding" -> "refund")
    for q in ("refunds?", "refunding an order"):
        m_ref = search_knowledge_chunks(
            app_role_db,
            tenant_id=tenant.id,
            department_id=support.id,
            query=q,
        )
        assert len(m_ref) == 1
        assert m_ref[0].title == "Refund Guidelines"


def test_fts_ranks_by_relevance_not_created_at(app_role_db: Session) -> None:
    tenant, support, _finance, _ops = _seed_tenant_and_depts(app_role_db)

    older_relevant = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="Dedicated Refund Escalation SOP",
        content=(
            "Refund escalation procedure: verify refund eligibility, calculate refund total, "
            "and issue the customer refund within 2 business days."
        ),
        source=DocumentSource.UPLOAD,
    )
    newer_weak = upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="General Quarterly Newsletter",
        content=(
            "Welcome to our quarterly newsletter covering office events, team updates, "
            "new product launches, and a brief mention of a customer refund policy update."
        ),
        source=DocumentSource.UPLOAD,
    )

    # Make the highly relevant chunk explicitly OLDER than the weak chunk
    old_ts = datetime.now(UTC) - timedelta(days=30)
    new_ts = datetime.now(UTC)
    app_role_db.execute(
        text("UPDATE doc_chunks SET created_at = :ts WHERE document_id = :did"),
        {"ts": old_ts, "did": older_relevant.id},
    )
    app_role_db.execute(
        text("UPDATE doc_chunks SET created_at = :ts WHERE document_id = :did"),
        {"ts": new_ts, "did": newer_weak.id},
    )
    app_role_db.flush()

    results = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        query="customer refund escalation procedure",
        top_k=5,
    )
    assert len(results) == 2
    assert results[0].title == "Dedicated Refund Escalation SOP"
    assert results[0].rank > results[1].rank


def test_fts_long_email_matches_25th_word_keyword(app_role_db: Session) -> None:
    tenant, support, _finance, _ops = _seed_tenant_and_depts(app_role_db)

    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="SSO SAML Troubleshooting",
        content="If enterprise SAML single sign-on fails with an ACS mismatch error, re-upload the IdP metadata XML.",
        source=DocumentSource.UPLOAD,
    )

    # 30-word email where NONE of the first 24 words appear in the doc and "SAML" is literally word #25 (index 24)
    long_email = (
        "Hello team hope you are having a wonderful week so far today "
        "we noticed something strange while onboarding our newest customer users yesterday afternoon "
        "SAML configuration keeps rejecting login attempts."
    )
    words = long_email.split()
    assert words[24] == "SAML"

    results = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        query=long_email,
        top_k=5,
    )
    assert len(results) == 1
    assert results[0].title == "SSO SAML Troubleshooting"


def test_fts_very_long_query_200_plus_unique_words_capped_at_max_lexemes(app_role_db: Session) -> None:
    tenant, support, _finance, _ops = _seed_tenant_and_depts(app_role_db)

    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="Kubernetes Pod OOMKilled Runbook",
        content=(
            "When a production Kubernetes container fails with OOMKilled exit code 137, "
            "increase memory limits in the deployment manifest and inspect heap dumps."
        ),
        source=DocumentSource.UPLOAD,
    )

    # Build a 240+ unique-word query where relevant keywords appear within the first 32 unique
    # non-stop-word lexemes (at position #18) and 220+ extra unique words follow.
    prefix_words = [f"uniquetoken{i}" for i in range(1, 18)]
    target_words = ["kubernetes", "oomkilled", "container"]
    suffix_words = [f"uniquetoken{i}" for i in range(18, 250)]
    all_words = prefix_words + target_words + suffix_words
    assert len(set(all_words)) >= 250

    huge_query = " ".join(all_words)
    settings = get_settings()
    assert settings.retrieval_max_lexemes == 32

    extracted = _extract_safe_lexemes(app_role_db, huge_query)
    assert len(extracted) == 32
    assert extracted[0] == "uniquetoken1"
    assert "oomkil" in extracted or "kubernet" in extracted

    results = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        query=huge_query,
        top_k=5,
    )
    assert len(results) == 1
    assert results[0].title == "Kubernetes Pod OOMKilled Runbook"


def test_fts_tenant_rls_and_department_scoping_under_app_role(app_role_db: Session) -> None:
    # Prove this test runs under the non-superuser app role with RLS genuinely enforced
    role_row = app_role_db.execute(
        text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).first()
    assert role_row is not None
    assert role_row[0] == "opsmind_app"
    assert role_row[1] is False
    assert role_row[2] is False

    tenant_a, support_a, finance_a, _ops_a = _seed_tenant_and_depts(app_role_db)

    # Seed Tenant A docs: shared, support private, finance private, and non-READY (FAILED)
    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant_a.id,
        department_id=None,
        scope="company_shared",
        title="Shared Invoice Guide",
        content="General company invoice submission guidelines for all teams.",
        source=DocumentSource.UPLOAD,
    )
    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant_a.id,
        department_id=support_a.id,
        scope="department_private",
        title="Support Invoice FAQ",
        content="How support agents answer customer invoice questions.",
        source=DocumentSource.UPLOAD,
    )
    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant_a.id,
        department_id=finance_a.id,
        scope="department_private",
        title="Finance Secret Invoice Ledger",
        content="Confidential vendor invoice bank routing numbers for finance only.",
        source=DocumentSource.UPLOAD,
    )
    failed_doc = Document(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        department_id=support_a.id,
        scope="department_private",
        source=DocumentSource.UPLOAD,
        title="Broken Invoice Draft",
        status=DocumentStatus.FAILED,
    )
    app_role_db.add(failed_doc)
    app_role_db.flush()
    app_role_db.add(
        DocChunk(
            tenant_id=tenant_a.id,
            document_id=failed_doc.id,
            chunk_index=0,
            content="Broken invoice content that must never be retrieved.",
        )
    )
    app_role_db.flush()

    # Seed Tenant B doc with the exact same keyword under Tenant B's RLS context
    tenant_b = Tenant(
        id=uuid.uuid4(),
        name="Other Tenant B",
        slug=f"ret-b-{uuid.uuid4().hex[:8]}",
        region=Region.US,
    )
    set_rls_context(app_role_db, tenant_id=tenant_b.id)
    app_role_db.add(tenant_b)
    app_role_db.flush()
    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant_b.id,
        department_id=None,
        scope="company_shared",
        title="Tenant B Invoice Secret",
        content="Tenant B confidential invoice terms.",
        source=DocumentSource.UPLOAD,
    )

    # Switch back to Tenant A RLS context
    set_rls_context(app_role_db, tenant_id=tenant_a.id)

    # 1. Support with include_company_shared=True -> sees Shared + Support private ONLY
    sup_with_shared = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant_a.id,
        department_id=support_a.id,
        query="invoice",
        include_company_shared=True,
    )
    titles_shared = {r.title for r in sup_with_shared}
    assert titles_shared == {"Shared Invoice Guide", "Support Invoice FAQ"}
    assert "Finance Secret Invoice Ledger" not in titles_shared
    assert "Broken Invoice Draft" not in titles_shared
    assert "Tenant B Invoice Secret" not in titles_shared

    # 2. Support with include_company_shared=False -> sees Support private ONLY
    sup_private_only = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant_a.id,
        department_id=support_a.id,
        query="invoice",
        include_company_shared=False,
    )
    assert {r.title for r in sup_private_only} == {"Support Invoice FAQ"}

    # 3. Cross-tenant RLS check: while RLS context is Tenant A, querying with tenant_id=tenant_b returns 0
    cross_tenant = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant_b.id,
        department_id=None,
        query="invoice",
        include_company_shared=True,
    )
    assert cross_tenant == []


def test_policy_qa_unknown_question_triggers_idk_and_approval_escalation(
    app_role_db: Session,
) -> None:
    tenant, _support, _finance, ops = _seed_tenant_and_depts(app_role_db)

    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=None,
        scope="company_shared",
        title="Annual PTO Policy",
        content="Employees receive 20 days of annual paid time off each year.",
        source=DocumentSource.UPLOAD,
    )

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
                    "text": "Hallucinated answer without sources",
                    "confidence": 0.92,
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
    assert finished.output["agent_1"]["has_reliable_source"] is False
    assert finished.status == RunStatus.WAITING_APPROVAL

    approvals = app_role_db.scalars(
        select(WorkflowApproval).where(
            WorkflowApproval.tenant_id == tenant.id,
            WorkflowApproval.run_id == run.id,
        )
    ).all()
    assert len(approvals) == 1
    assert approvals[0].status == WorkflowApprovalStatus.PENDING


def test_fts_single_word_overlap_travel_allowance_rejected_for_mars_relocation(
    app_role_db: Session,
) -> None:
    """A chunk mentioning 'travel allowance' must NOT match 'What is the Mars relocation allowance?'."""
    tenant, _support, _finance, ops = _seed_tenant_and_depts(app_role_db)

    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=None,
        scope="company_shared",
        title="Business Travel Allowance Policy",
        content="Employees may claim a daily travel allowance of $75 for meals during business trips.",
        source=DocumentSource.UPLOAD,
    )

    # Only 1 of 3 query lexemes ('allow') matches; focused-query minimum ratio (2/3) rejects it
    results = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=ops.id,
        query="What is the Mars relocation allowance?",
        include_company_shared=True,
    )
    assert results == []

    # Whereas asking specifically about "travel allowance?" (2/2 lexemes) matches
    travel_res = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=ops.id,
        query="What is the travel allowance?",
        include_company_shared=True,
    )
    assert len(travel_res) == 1
    assert travel_res[0].title == "Business Travel Allowance Policy"


def test_fts_hostile_special_characters_and_operators_never_crash(
    app_role_db: Session,
) -> None:
    """Arbitrary user input with tsquery operators, quotes, colons, parentheses, or stop-words never errors."""
    tenant, support, _finance, _ops = _seed_tenant_and_depts(app_role_db)

    upsert_knowledge_document(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        scope="department_private",
        title="Refund Policy",
        content="Standard refund policy rules for customer orders.",
        source=DocumentSource.UPLOAD,
    )

    empty_or_stop_inputs = [
        "",
        "   ",
        "what is the and or to in of for?",
        "&&& ||| ::: ''' \"\"\" ((())) ! <-> *",
        "':* & | ! () \"\"",
        "((not closed & | 'single\"double:colon",
    ]
    for raw_q in empty_or_stop_inputs:
        res = search_knowledge_chunks(
            app_role_db,
            tenant_id=tenant.id,
            department_id=support.id,
            query=raw_q,
        )
        assert res == [], f"Expected 0 results for {raw_q!r}"

    # Hostile operators around valid keywords still match safely without syntax errors
    matched = search_knowledge_chunks(
        app_role_db,
        tenant_id=tenant.id,
        department_id=support.id,
        query="\"refund\" & (policy | ' injection '):* ! ((()))",
    )
    assert len(matched) == 1
    assert matched[0].title == "Refund Policy"
