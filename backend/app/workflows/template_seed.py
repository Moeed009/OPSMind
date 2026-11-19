"""Idempotent seed data for the 6 approved department workflow templates and built-in EvalSets."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import WorkflowTemplate
from app.db.models.eval_set import EvalSet
from app.workflows.definition import WorkflowDefinition

SEED_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "support_reply",
        "department_type": "support",
        "name": "Support Reply",
        "description": "Classify incoming support emails, retrieve customer orders & KB help docs, draft reply, escalate risky cases for human approval, and send via Gmail/HubSpot/Slack.",
        "required_connectors": ["gmail", "hubspot", "slack"],
        "definition": {
            "trigger": {"type": "event", "source": "gmail.new_email"},
            "nodes": [
                {
                    "id": "classify_1",
                    "type": "classify",
                    "label": "Classify Message Type & Priority",
                    "labels": ["billing", "refund", "technical", "general"],
                    "prompt": "Classify message type and priority.",
                    "confidence_threshold": 0.7,
                },
                {
                    "id": "retrieve_1",
                    "type": "retrieve",
                    "label": "Retrieve Orders & Help Docs",
                    "query_template": "{{trigger.subject}} {{trigger.body}}",
                    "top_k": 5,
                    "include_company_shared": True,
                },
                {
                    "id": "agent_1",
                    "type": "agent",
                    "label": "Draft Support Reply",
                    "prompt": "Draft a helpful customer support reply using retrieved Knowledge Hub context.",
                    "confidence_threshold": 0.75,
                },
                {
                    "id": "cond_1",
                    "type": "condition",
                    "label": "Needs Approval?",
                    "expression": "classify_1.label == 'refund' or agent_1.confidence < 0.75",
                },
                {
                    "id": "approve_1",
                    "type": "approval",
                    "label": "Human Approval",
                    "role": "reviewer",
                    "timeout_hours": 24,
                },
                {
                    "id": "action_1",
                    "type": "action",
                    "label": "Send Email Reply",
                    "action": "gmail.send_reply",
                    "params": {"body": "{{agent_1.text}}"},
                },
            ],
            "edges": [
                {"source": "classify_1", "target": "retrieve_1"},
                {"source": "retrieve_1", "target": "agent_1"},
                {"source": "agent_1", "target": "cond_1"},
                {"source": "cond_1", "target": "approve_1", "label": "true"},
                {"source": "cond_1", "target": "action_1", "label": "false"},
                {"source": "approve_1", "target": "action_1"},
            ],
        },
    },
    {
        "key": "finance_supplier_invoice",
        "department_type": "finance",
        "name": "Supplier Invoice Processing",
        "description": "Extract invoice fields, retrieve purchase order records from Knowledge Hub, verify match, route mismatches for human approval, and create vendor bill in Zoho Books.",
        "required_connectors": ["gmail", "google_drive", "zoho_books"],
        "definition": {
            "trigger": {"type": "event", "source": "gmail.new_email"},
            "nodes": [
                {
                    "id": "extract_1",
                    "type": "extract",
                    "label": "Extract Invoice Fields",
                    "prompt": "Extract invoice_number, total_amount, due_date, po_number, and field confidence.",
                    "output_schema": {
                        "invoice_number": "string",
                        "total_amount": "number",
                        "due_date": "string",
                        "po_number": "string",
                    },
                    "confidence_threshold": 0.8,
                },
                {
                    "id": "retrieve_1",
                    "type": "retrieve",
                    "label": "Retrieve Purchase Order Records",
                    "query_template": "{{extract_1.data.po_number}} {{extract_1.data.invoice_number}}",
                    "top_k": 5,
                    "include_company_shared": False,
                },
                {
                    "id": "cond_1",
                    "type": "condition",
                    "label": "Match Verification",
                    "expression": "retrieve_1.match_count > 0 and extract_1.confidence >= 0.8",
                },
                {
                    "id": "approve_1",
                    "type": "approval",
                    "label": "Human Approval (Mismatch)",
                    "role": "reviewer",
                    "timeout_hours": 48,
                },
                {
                    "id": "action_1",
                    "type": "action",
                    "label": "Create Vendor Bill",
                    "action": "zoho_books.create_bill",
                    "params": {
                        "bill_number": "{{extract_1.data.invoice_number}}",
                        "total_amount": "{{extract_1.data.total_amount}}",
                        "due_date": "{{extract_1.data.due_date}}",
                    },
                },
            ],
            "edges": [
                {"source": "extract_1", "target": "retrieve_1"},
                {"source": "retrieve_1", "target": "cond_1"},
                {"source": "cond_1", "target": "action_1", "label": "true"},
                {"source": "cond_1", "target": "approve_1", "label": "false"},
                {"source": "approve_1", "target": "action_1"},
            ],
        },
    },
    {
        "key": "sales_lead_management",
        "department_type": "sales",
        "name": "Sales Lead Management",
        "description": "Classify inbound lead quality, extract company & contact details, check CRM duplicates in Knowledge Hub, draft follow-up, optional human approval, and update HubSpot/Gmail/Calendar.",
        "required_connectors": ["gmail", "hubspot", "google_calendar"],
        "definition": {
            "trigger": {"type": "event", "source": "gmail.new_email"},
            "nodes": [
                {
                    "id": "classify_1",
                    "type": "classify",
                    "label": "Classify Lead Quality & Priority",
                    "labels": ["high", "medium", "low"],
                    "prompt": "Classify inbound sales lead quality.",
                    "confidence_threshold": 0.7,
                },
                {
                    "id": "extract_1",
                    "type": "extract",
                    "label": "Extract Company & Contact",
                    "prompt": "Extract company, contact_email, contact_name, and requirements.",
                    "output_schema": {
                        "company": "string",
                        "contact_email": "string",
                        "requirements": "string",
                    },
                },
                {
                    "id": "retrieve_1",
                    "type": "retrieve",
                    "label": "Duplicate Check in CRM Hub",
                    "query_template": "{{extract_1.data.company}} {{extract_1.data.contact_email}}",
                    "top_k": 5,
                    "include_company_shared": False,
                },
                {
                    "id": "agent_1",
                    "type": "agent",
                    "label": "Draft Follow-up",
                    "prompt": "Draft a personalized sales follow-up email based on requirements.",
                },
                {
                    "id": "approve_1",
                    "type": "approval",
                    "label": "Human Approval (Optional)",
                    "role": "reviewer",
                    "timeout_hours": 24,
                },
                {
                    "id": "action_1",
                    "type": "action",
                    "label": "Update CRM & Send Reply",
                    "action": "hubspot.create_contact",
                    "params": {
                        "email": "{{extract_1.data.contact_email}}",
                        "company": "{{extract_1.data.company}}",
                    },
                },
            ],
            "edges": [
                {"source": "classify_1", "target": "extract_1"},
                {"source": "extract_1", "target": "retrieve_1"},
                {"source": "retrieve_1", "target": "agent_1"},
                {"source": "agent_1", "target": "approve_1"},
                {"source": "approve_1", "target": "action_1"},
            ],
        },
    },
    {
        "key": "legal_contract_review",
        "department_type": "legal",
        "name": "Contract Review",
        "description": "Extract contract terms from Google Drive documents, retrieve company policies & standard clauses, summarize risky clauses, require mandatory human approval, and notify via Slack or Gmail.",
        "required_connectors": ["google_drive", "gmail", "slack"],
        "definition": {
            "trigger": {"type": "event", "source": "google_drive.new_file"},
            "nodes": [
                {
                    "id": "extract_1",
                    "type": "extract",
                    "label": "Extract Contract Terms",
                    "prompt": "Extract parties, effective_date, payment_terms, and renewal_terms.",
                    "output_schema": {
                        "parties": "string",
                        "dates": "string",
                        "payment_terms": "string",
                        "renewal_terms": "string",
                    },
                },
                {
                    "id": "retrieve_1",
                    "type": "retrieve",
                    "label": "Retrieve Policies & Standard Clauses",
                    "query_template": "{{extract_1.data.payment_terms}} {{extract_1.data.renewal_terms}}",
                    "top_k": 5,
                    "include_company_shared": True,
                },
                {
                    "id": "agent_1",
                    "type": "agent",
                    "label": "Summarize Risky Clauses",
                    "prompt": "Compare extracted terms against company policies & standard clauses and summarize risks.",
                },
                {
                    "id": "approve_1",
                    "type": "approval",
                    "label": "Mandatory Legal Human Approval",
                    "role": "reviewer",
                    "timeout_hours": 48,
                },
                {
                    "id": "action_1",
                    "type": "action",
                    "label": "Send Summary via Slack",
                    "action": "slack.post_message",
                    "params": {"channel": "#legal-reviews", "message": "{{agent_1.text}}"},
                },
            ],
            "edges": [
                {"source": "extract_1", "target": "retrieve_1"},
                {"source": "retrieve_1", "target": "agent_1"},
                {"source": "agent_1", "target": "approve_1"},
                {"source": "approve_1", "target": "action_1"},
            ],
        },
    },
    {
        "key": "ops_weekly_report",
        "department_type": "operations",
        "name": "Weekly Report",
        "description": "Every Monday at 09:00, aggregate workflow run/ticket/lead counts across departments, draft weekly report, optional human approval, and send via Slack or Gmail.",
        "required_connectors": ["slack", "google_drive", "gmail"],
        "definition": {
            "trigger": {"type": "schedule", "mode": "weekly", "time": "09:00", "weekdays": [0]},
            "nodes": [
                {
                    "id": "retrieve_1",
                    "type": "retrieve",
                    "label": "Aggregate Operational Metrics",
                    "query_template": "weekly aggregate summary",
                    "top_k": 5,
                    "include_company_shared": True,
                    "mode": "aggregate_ops",
                },
                {
                    "id": "agent_1",
                    "type": "agent",
                    "label": "Draft Weekly Report",
                    "prompt": "Summarize aggregate workflow run counts, ticket counts, and lead counts into a concise weekly report.",
                },
                {
                    "id": "approve_1",
                    "type": "approval",
                    "label": "Human Approval (Optional)",
                    "role": "reviewer",
                    "timeout_hours": 24,
                },
                {
                    "id": "action_1",
                    "type": "action",
                    "label": "Send Weekly Report",
                    "action": "slack.post_message",
                    "params": {"channel": "#ops-weekly", "message": "{{agent_1.text}}"},
                },
            ],
            "edges": [
                {"source": "retrieve_1", "target": "agent_1"},
                {"source": "agent_1", "target": "approve_1"},
                {"source": "approve_1", "target": "action_1"},
            ],
        },
    },
    {
        "key": "ops_policy_qa",
        "department_type": "operations",
        "name": "Policy Q&A",
        "description": "Retrieve tenant-specific policy documents, answer with source references, and return 'I don't know' + human escalation if no reliable source is found.",
        "required_connectors": ["slack", "google_drive", "gmail"],
        "definition": {
            "trigger": {"type": "manual"},
            "nodes": [
                {
                    "id": "retrieve_1",
                    "type": "retrieve",
                    "label": "Retrieve Tenant Policy Documents",
                    "query_template": "{{trigger.question}}",
                    "top_k": 5,
                    "include_company_shared": True,
                },
                {
                    "id": "agent_1",
                    "type": "agent",
                    "label": "Answer with Source References",
                    "prompt": "Answer the employee policy question strictly using retrieved policy documents and cite sources.",
                    "require_sources": True,
                },
                {
                    "id": "cond_1",
                    "type": "condition",
                    "label": "Reliable Source Available?",
                    "expression": "retrieve_1.match_count > 0 and agent_1.has_reliable_source == True",
                },
                {
                    "id": "action_1",
                    "type": "action",
                    "label": "Return Answer",
                    "action": "slack.post_message",
                    "params": {"channel": "#policy-qa", "message": "{{agent_1.text}}"},
                },
                {
                    "id": "approve_1",
                    "type": "approval",
                    "label": "Escalate Unknown Policy Question",
                    "role": "reviewer",
                    "timeout_hours": 24,
                },
            ],
            "edges": [
                {"source": "retrieve_1", "target": "agent_1"},
                {"source": "agent_1", "target": "cond_1"},
                {"source": "cond_1", "target": "action_1", "label": "true"},
                {"source": "cond_1", "target": "approve_1", "label": "false"},
            ],
        },
    },
]

APPROVED_TEMPLATE_KEYS: set[str] = {t["key"] for t in SEED_TEMPLATES}

BUILTIN_EVAL_CASES: dict[str, list[dict[str, Any]]] = {
    "support_reply": [
        {"name": "billing_question", "input": {"subject": "Invoice question", "body": "Where is my receipt?"}},
        {"name": "refund_request", "input": {"subject": "Refund", "body": "I want a refund for order #102"}},
        {"name": "technical_help", "input": {"subject": "Login issue", "body": "Cannot reset password"}},
    ],
    "finance_supplier_invoice": [
        {"name": "valid_invoice", "input": {"subject": "INV-1001", "body": "Invoice INV-1001 for PO-500 total $250"}},
        {"name": "missing_po", "input": {"subject": "INV-1002", "body": "Invoice without PO reference"}},
        {"name": "amount_mismatch", "input": {"subject": "INV-1003", "body": "Total $9999"}},
    ],
    "sales_lead_management": [
        {"name": "enterprise_lead", "input": {"subject": "Enterprise demo", "body": "Acme Corp, 200 seats"}},
        {"name": "smb_lead", "input": {"subject": "Pricing inquiry", "body": "Small team looking for plan"}},
        {"name": "spam_lead", "input": {"subject": "SEO services", "body": "Buy backlinks"}},
    ],
    "legal_contract_review": [
        {"name": "standard_nda", "input": {"file_name": "NDA.pdf", "body": "Mutual NDA 12 months"}},
        {"name": "unlimited_liability", "input": {"file_name": "MSA.pdf", "body": "Unlimited indemnity clause"}},
        {"name": "auto_renewal", "input": {"file_name": "Vendor.pdf", "body": "Auto-renews 90 days notice"}},
    ],
    "ops_weekly_report": [
        {"name": "weekly_summary", "input": {"period": "weekly"}},
        {"name": "zero_runs", "input": {"period": "weekly", "note": "quiet week"}},
        {"name": "high_volume", "input": {"period": "weekly", "note": "peak week"}},
    ],
    "ops_policy_qa": [
        {"name": "pto_policy", "input": {"question": "How many annual PTO days do employees get?"}},
        {"name": "remote_work", "input": {"question": "What is the remote work stipend policy?"}},
        {"name": "unknown_policy", "input": {"question": "What is the Mars relocation allowance?"}},
    ],
}


def ensure_builtin_eval_set_for_workflow(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    template_key: str | None,
) -> EvalSet | None:
    if not template_key or template_key not in BUILTIN_EVAL_CASES:
        return None
    existing = db.scalar(
        select(EvalSet).where(
            EvalSet.tenant_id == tenant_id,
            EvalSet.workflow_id == workflow_id,
        )
    )
    if existing is not None:
        return existing
    cases = BUILTIN_EVAL_CASES[template_key]
    ev_set = EvalSet(
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        name=f"{template_key}:{workflow_id.hex[:8]}",
        job_type=template_key,
        cases=cases,
        case_count=len(cases),
        pass_threshold=Decimal("0.7000"),
    )
    db.add(ev_set)
    db.flush()
    return ev_set


def seed_workflow_templates(db: Session) -> dict[str, int]:
    """Upsert the 6 approved templates and deactivate any unapproved built-in templates."""
    created = 0
    updated = 0
    deactivated = 0

    all_existing = db.scalars(select(WorkflowTemplate)).all()
    for row in all_existing:
        if row.key not in APPROVED_TEMPLATE_KEYS and row.is_active:
            row.is_active = False
            deactivated += 1

    for tpl in SEED_TEMPLATES:
        defn = WorkflowDefinition.model_validate(tpl["definition"])
        row = db.scalar(select(WorkflowTemplate).where(WorkflowTemplate.key == tpl["key"]))
        if row is None:
            db.add(
                WorkflowTemplate(
                    key=tpl["key"],
                    department_type=tpl["department_type"],
                    name=tpl["name"],
                    description=tpl["description"],
                    definition=defn.model_dump(),
                    required_connectors=tpl["required_connectors"],
                    is_active=True,
                )
            )
            created += 1
        else:
            row.department_type = tpl["department_type"]
            row.name = tpl["name"]
            row.description = tpl["description"]
            row.definition = defn.model_dump()
            row.required_connectors = tpl["required_connectors"]
            row.is_active = True
            updated += 1
    db.flush()
    return {"created": created, "updated": updated, "deactivated": deactivated}
