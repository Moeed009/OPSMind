"""Phase 4C tests: Shared Google Sheets OAuth2Connector, all 6 registered actions, isolation, token refresh, and ActionLog idempotency."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from urllib.parse import unquote

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import crypto
from app.connectors.base import TokenSet
from app.connectors.errors import ReauthRequired
from app.connectors.google_sheets.connector import GoogleSheetsConnector
from app.connectors.http import HttpResponse
from app.connectors.registry import get_connector_class
from app.core.config import get_settings
from app.db.enums import (
    ActionStatus,
    ConnectionStatus,
    Region,
    Role,
    RunStatus,
    TriggerSource,
    WorkflowVersionStatus,
)
from app.db.models import (
    ActionLog,
    Connection,
    Membership,
    Run,
    Tenant,
    User,
    Workflow,
    WorkflowVersion,
)
from app.db.rls import set_rls_context
from app.db.session import get_engine
from app.schemas.department import DepartmentCreate
from app.services.department import create_department
from app.workflows.action_registry import execute_registered_action, is_action_available
from app.workflows.actions import ActionDispatcher


class _FakeHttp:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs) -> HttpResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.handler(method, url, kwargs)


@pytest.fixture()
def db_session():
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


def _encrypt_tokens(tenant_id: uuid.UUID, connection_id: uuid.UUID, tokens: dict[str, Any]):
    settings = get_settings()
    keyring = crypto.KeyRing.from_env(
        {
            "CONNECTOR_ENCRYPTION_KEYS": settings.connector_encryption_keys,
            "CONNECTOR_ENCRYPTION_CURRENT_KEY_ID": settings.connector_encryption_current_key_id,
        }
    )
    ct, kid = crypto.encrypt(tokens, tenant_id=tenant_id, connection_id=connection_id, keyring=keyring)
    return ct.encode("ascii"), kid


def _seed_workflow_run(db: Session, *, department_id: uuid.UUID | None = None):
    user = User(id=uuid.uuid4(), email=f"sheets-{uuid.uuid4().hex[:6]}@opsmind.dev")
    set_rls_context(db, user_id=user.id)
    db.add(user)
    db.flush()

    tenant = Tenant(
        id=uuid.uuid4(),
        name="Sheets Co",
        slug=f"sheets-{uuid.uuid4().hex[:6]}",
        region=Region.US,
    )
    set_rls_context(db, tenant_id=tenant.id, user_id=user.id)
    db.add(tenant)
    db.flush()

    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER))
    db.flush()

    dept = create_department(
        db,
        tenant_id=tenant.id,
        actor_id=user.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Human Resources", template_key="hr"),
    )
    target_dept_id = department_id or dept.id

    wf = Workflow(
        tenant_id=tenant.id,
        department_id=target_dept_id,
        name=f"Sheets WF {uuid.uuid4().hex[:6]}",
        created_by=user.id,
    )
    db.add(wf)
    db.flush()

    ver = WorkflowVersion(
        tenant_id=tenant.id,
        workflow_id=wf.id,
        version_number=1,
        status=WorkflowVersionStatus.PUBLISHED,
        definition={"trigger": {"type": "manual"}, "nodes": [{"id": "a1", "type": "action", "label": "S", "action": "sheets.read_rows", "params": {}}], "edges": []},
        created_by=user.id,
    )
    db.add(ver)
    db.flush()

    run = Run(
        tenant_id=tenant.id,
        department_id=target_dept_id,
        workflow_id=wf.id,
        workflow_version_id=ver.id,
        status=RunStatus.RUNNING,
        trigger_source=TriggerSource.MANUAL,
        trace_id=uuid.uuid4().hex,
        input={},
    )
    db.add(run)
    db.flush()
    return user, tenant, dept, run


def test_google_sheets_oauth_flow_and_token_refresh():
    assert get_connector_class("google_sheets") is GoogleSheetsConnector

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> HttpResponse:
        if "oauth2.googleapis.com/token" in url:
            grant = kwargs["form"]["grant_type"]
            if grant == "authorization_code":
                return HttpResponse(
                    200,
                    {
                        "access_token": "ya29.access1",
                        "refresh_token": "1//refresh1",
                        "expires_in": 3600,
                        "scope": "https://www.googleapis.com/auth/spreadsheets",
                    },
                )
            if kwargs["form"]["refresh_token"] == "revoked":
                return HttpResponse(400, {"error": "invalid_grant"})
            return HttpResponse(
                200,
                {"access_token": "ya29.access2", "expires_in": 3600},
            )
        if "userinfo" in url:
            return HttpResponse(200, {"email": "HR.Ops@Company.com"})
        return HttpResponse(404, {})

    conn = GoogleSheetsConnector(_FakeHttp(handler), "client-1", "secret-1")
    url = unquote(conn.authorize_url(state="st1", code_challenge="cc1", redirect_uri="https://app/cb"))
    assert "https://www.googleapis.com/auth/spreadsheets" in url
    assert "https://www.googleapis.com/auth/userinfo.email" in url
    assert "gmail" not in url

    tokens = asyncio.run(conn.exchange_code("code-1", "ver-1", "https://app/cb"))
    assert tokens.access_token == "ya29.access1"
    assert tokens.refresh_token == "1//refresh1"

    account_id = asyncio.run(conn.fetch_account_id(tokens))
    assert account_id == "hr.ops@company.com"

    refreshed = asyncio.run(conn.refresh(tokens))
    assert refreshed.access_token == "ya29.access2"
    assert refreshed.refresh_token == "1//refresh1"

    with pytest.raises(ReauthRequired):
        asyncio.run(conn.refresh(TokenSet(access_token="x", refresh_token="revoked")))


def test_all_six_google_sheets_actions_registered_and_idempotent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    for key in (
        "sheets.create_spreadsheet",
        "sheets.create_worksheet",
        "sheets.read_rows",
        "sheets.append_row",
        "sheets.update_row",
        "sheets.search_rows",
    ):
        assert is_action_available(key) is True

    _user, tenant, dept, run = _seed_workflow_run(db_session)
    conn_id = uuid.uuid4()
    ct, kid = _encrypt_tokens(
        tenant.id,
        conn_id,
        {"access_token": "ya29.valid", "refresh_token": "1//ref"},
    )
    conn = Connection(
        id=conn_id,
        tenant_id=tenant.id,
        department_id=dept.id,
        provider="google_sheets",
        display_name="HR Sheets",
        external_account_id="hr-sheets@company.com",
        status=ConnectionStatus.HEALTHY,
        credentials_ciphertext=ct,
        credentials_key_id=kid,
    )
    db_session.add(conn)
    db_session.flush()

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> HttpResponse:
        if method == "POST" and url == "https://sheets.googleapis.com/v4/spreadsheets":
            return HttpResponse(
                200,
                {
                    "spreadsheetId": "sheet-123",
                    "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/sheet-123",
                    "properties": {"title": kwargs["json"]["properties"]["title"]},
                    "sheets": [{"properties": {"sheetId": 0, "title": "Candidates"}}],
                },
            )
        if ":batchUpdate" in url:
            return HttpResponse(
                200,
                {
                    "spreadsheetId": "sheet-123",
                    "replies": [{"addSheet": {"properties": {"sheetId": 42, "title": "Shortlisted"}}}],
                },
            )
        if ":append" in url:
            return HttpResponse(
                200,
                {
                    "spreadsheetId": "sheet-123",
                    "tableRange": "Candidates!A1:D1",
                    "updates": {"updatedRange": "Candidates!A2:D2", "updatedRows": 1},
                },
            )
        if method == "PUT" and "/values/" in url:
            return HttpResponse(
                200,
                {"spreadsheetId": "sheet-123", "updatedRange": "Candidates!A2:D2", "updatedRows": 1},
            )
        if method == "GET" and "/values/" in url:
            return HttpResponse(
                200,
                {
                    "range": "Candidates!A1:D3",
                    "values": [
                        ["ID", "Name", "Role", "Status"],
                        ["c1", "Ayesha Khan", "Backend Engineer", "approved"],
                        ["c2", "Bilal Ahmed", "Designer", "pending"],
                    ],
                },
            )
        return HttpResponse(404, {"error": {"message": "unexpected"}})

    fake_http = _FakeHttp(handler)
    monkeypatch.setattr(
        "app.workflows.actions._build_sheets_connector",
        lambda: GoogleSheetsConnector(fake_http, "cid", "csec"),
    )

    # 1. create_spreadsheet
    r1 = execute_registered_action(
        db_session,
        tenant_id=tenant.id,
        department_id=dept.id,
        run_id=run.id,
        run_step_id=None,
        action_key="sheets.create_spreadsheet",
        params={"title": "HR Hiring 2026", "idempotency_key": "k1"},
        context={},
    )
    assert r1.success is True
    assert r1.response_data["spreadsheet_id"] == "sheet-123"

    # 2. create_worksheet
    r2 = execute_registered_action(
        db_session,
        tenant_id=tenant.id,
        department_id=dept.id,
        run_id=run.id,
        run_step_id=None,
        action_key="sheets.create_worksheet",
        params={"spreadsheet_id": "sheet-123", "title": "Shortlisted", "idempotency_key": "k2"},
        context={},
    )
    assert r2.success is True
    assert r2.response_data["worksheet_id"] == "42"

    # 3. read_rows
    r3 = execute_registered_action(
        db_session,
        tenant_id=tenant.id,
        department_id=dept.id,
        run_id=run.id,
        run_step_id=None,
        action_key="sheets.read_rows",
        params={"spreadsheet_id": "sheet-123", "range": "Candidates!A1:D3", "idempotency_key": "k3"},
        context={},
    )
    assert r3.success is True
    assert r3.response_data["row_count"] == 3

    # 4. append_row + idempotent replay
    r4 = execute_registered_action(
        db_session,
        tenant_id=tenant.id,
        department_id=dept.id,
        run_id=run.id,
        run_step_id=None,
        action_key="sheets.append_row",
        params={
            "spreadsheet_id": "sheet-123",
            "range": "Candidates!A:D",
            "values": ["c1", "Ayesha Khan", "Backend Engineer", "approved"],
            "idempotency_key": "k4",
        },
        context={},
    )
    assert r4.success is True
    assert r4.external_ref == "Candidates!A2:D2"
    calls_before_replay = len(fake_http.calls)

    r4_replay = execute_registered_action(
        db_session,
        tenant_id=tenant.id,
        department_id=dept.id,
        run_id=run.id,
        run_step_id=None,
        action_key="sheets.append_row",
        params={
            "spreadsheet_id": "sheet-123",
            "range": "Candidates!A:D",
            "values": ["c1", "Ayesha Khan", "Backend Engineer", "approved"],
            "idempotency_key": "k4",
        },
        context={},
    )
    assert r4_replay.idempotent_replay is True
    assert len(fake_http.calls) == calls_before_replay

    # Verify candidate PII ('Ayesha Khan') is not stored in ActionLog.request
    log_k4 = db_session.scalar(
        select(ActionLog).where(ActionLog.tenant_id == tenant.id, ActionLog.idempotency_key == "k4")
    )
    assert log_k4 is not None
    assert log_k4.status == ActionStatus.SUCCEEDED
    assert "Ayesha Khan" not in json.dumps(log_k4.request)

    # 5. update_row
    r5 = execute_registered_action(
        db_session,
        tenant_id=tenant.id,
        department_id=dept.id,
        run_id=run.id,
        run_step_id=None,
        action_key="sheets.update_row",
        params={
            "spreadsheet_id": "sheet-123",
            "range": "Candidates!A2:D2",
            "values": ["c1", "Ayesha Khan", "Backend Engineer", "hired"],
            "idempotency_key": "k5",
        },
        context={},
    )
    assert r5.success is True
    assert r5.response_data["updated_rows"] == 1

    # 6. search_rows
    r6 = execute_registered_action(
        db_session,
        tenant_id=tenant.id,
        department_id=dept.id,
        run_id=run.id,
        run_step_id=None,
        action_key="sheets.search_rows",
        params={
            "spreadsheet_id": "sheet-123",
            "range": "Candidates!A1:D3",
            "query": "Ayesha",
            "column_index": 1,
            "idempotency_key": "k6",
        },
        context={},
    )
    assert r6.success is True
    assert r6.response_data["match_count"] == 1
    assert r6.response_data["matches"][0]["row_number"] == 2


def test_google_sheets_department_isolation_and_401_token_refresh(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    user, tenant, dept_hr, run = _seed_workflow_run(db_session)
    dept_sales = create_department(
        db_session,
        tenant_id=tenant.id,
        actor_id=user.id,
        actor_role=Role.OWNER,
        payload=DepartmentCreate(name="Sales", template_key="sales"),
    )

    # Company-wide connection (department_id=None) must NOT be usable by HR department
    company_cid = uuid.uuid4()
    ct_c, kid_c = _encrypt_tokens(tenant.id, company_cid, {"access_token": "ya29.company"})
    db_session.add(
        Connection(
            id=company_cid,
            tenant_id=tenant.id,
            department_id=None,
            provider="google_sheets",
            display_name="Company Sheets",
            external_account_id="company@co.com",
            status=ConnectionStatus.HEALTHY,
            credentials_ciphertext=ct_c,
            credentials_key_id=kid_c,
        )
    )
    # Sales connection must NOT be usable by HR department
    sales_cid = uuid.uuid4()
    ct_s, kid_s = _encrypt_tokens(tenant.id, sales_cid, {"access_token": "ya29.sales"})
    db_session.add(
        Connection(
            id=sales_cid,
            tenant_id=tenant.id,
            department_id=dept_sales.id,
            provider="google_sheets",
            display_name="Sales Sheets",
            external_account_id="sales@co.com",
            status=ConnectionStatus.HEALTHY,
            credentials_ciphertext=ct_s,
            credentials_key_id=kid_s,
        )
    )
    db_session.flush()

    res_isolated = ActionDispatcher.dispatch(
        db=db_session,
        tenant_id=tenant.id,
        run_id=run.id,
        run_step_id=None,
        action_type="sheets.read_rows",
        config={"spreadsheet_id": "s1", "range": "A1:B2", "idempotency_key": "iso-1"},
        context={},
        department_id=dept_hr.id,
    )
    assert res_isolated.success is False
    assert "Cross-department fallback is prohibited" in (res_isolated.error or "")

    # Now add HR department connection with expired access_token -> auto-refresh + re-encrypt
    hr_cid = uuid.uuid4()
    ct_hr, kid_hr = _encrypt_tokens(
        tenant.id, hr_cid, {"access_token": "expired_tok", "refresh_token": "1//hr_ref"}
    )
    hr_conn = Connection(
        id=hr_cid,
        tenant_id=tenant.id,
        department_id=dept_hr.id,
        provider="google_sheets",
        display_name="HR Sheets",
        external_account_id="hr@co.com",
        status=ConnectionStatus.HEALTHY,
        credentials_ciphertext=ct_hr,
        credentials_key_id=kid_hr,
    )
    db_session.add(hr_conn)
    db_session.flush()

    def refresh_handler(method: str, url: str, kwargs: dict[str, Any]) -> HttpResponse:
        if "oauth2.googleapis.com/token" in url:
            return HttpResponse(200, {"access_token": "fresh_tok", "expires_in": 3600})
        auth = (kwargs.get("headers") or {}).get("Authorization", "")
        if auth == "Bearer expired_tok":
            return HttpResponse(401, {"error": {"message": "expired"}})
        return HttpResponse(200, {"range": "A1:B2", "values": [["ok"]]})

    monkeypatch.setattr(
        "app.workflows.actions._build_sheets_connector",
        lambda: GoogleSheetsConnector(_FakeHttp(refresh_handler), "cid", "csec"),
    )

    res_refreshed = ActionDispatcher.dispatch(
        db=db_session,
        tenant_id=tenant.id,
        run_id=run.id,
        run_step_id=None,
        action_type="sheets.read_rows",
        config={"spreadsheet_id": "s1", "range": "A1:B2", "idempotency_key": "ref-1"},
        context={},
        department_id=dept_hr.id,
    )
    assert res_refreshed.success is True
    assert res_refreshed.response_data["values"] == [["ok"]]
