
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import crypto
from app.connectors.base import TokenSet
from app.connectors.errors import ConnectorError, RateLimited, TokenExpired
from app.connectors.google_sheets.connector import GoogleSheetsConnector
from app.connectors.httpx_client import HttpxClient
from app.connectors.zoho_books.connector import ZohoBooksConnector
from app.core.config import get_settings
from app.db.enums import ActionStatus, ConnectionStatus, ReviewTaskStatus, WorkflowApprovalStatus
from app.db.models.action_log import ActionLog
from app.db.models.connection import Connection
from app.db.models.review_task import ReviewTask
from app.db.models.workflow_approval import WorkflowApproval
from app.services import email as email_service

logger = logging.getLogger("opsmind.actions")

SLACK_POST_URL = "https://slack.com/api/chat.postMessage"
SLACK_CHANNELS_URL = "https://slack.com/api/conversations.list"


class ActionError(Exception):
    pass


@dataclass
class ActionResult:
    success: bool
    action_type: str
    external_ref: str | None = None
    response_data: dict[str, Any] | None = None
    error: str | None = None
    idempotent_replay: bool = False


def _keyring_from_settings() -> crypto.KeyRing:
    settings = get_settings()
    return crypto.KeyRing.from_env(
        {
            "CONNECTOR_ENCRYPTION_KEYS": settings.connector_encryption_keys,
            "CONNECTOR_ENCRYPTION_CURRENT_KEY_ID": settings.connector_encryption_current_key_id,
        }
    )


def _decrypt_connection_tokens(conn: Connection, provider_label: str) -> dict[str, Any]:
    if not conn.credentials_ciphertext or not conn.credentials_key_id:
        raise ActionError(f"{provider_label} connection has no stored credentials. Reconnect {provider_label}.")
    keyring = _keyring_from_settings()
    raw = conn.credentials_ciphertext
    ciphertext = raw if isinstance(raw, str) else bytes(raw).decode("ascii")
    return crypto.decrypt_json(
        ciphertext,
        conn.credentials_key_id,
        tenant_id=conn.tenant_id,
        connection_id=conn.id,
        keyring=keyring,
    )


def _slack_token(conn: Connection) -> str:
    tokens = _decrypt_connection_tokens(conn, "Slack")
    token = tokens.get("access_token")
    if not token:
        raise ActionError("Slack connection has no access token. Reconnect Slack.")
    return str(token)


def _run_async_action(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _build_sheets_connector() -> GoogleSheetsConnector:
    settings = get_settings()
    return GoogleSheetsConnector(
        HttpxClient(),
        settings.google_client_id or "placeholder_client_id",
        settings.google_client_secret or "placeholder_secret",
    )


def _build_zoho_connector() -> ZohoBooksConnector:
    settings = get_settings()
    return ZohoBooksConnector(
        HttpxClient(),
        settings.zoho_client_id or "placeholder_client_id",
        settings.zoho_client_secret or "placeholder_secret",
    )


def _sanitize_request_for_log(action_type: str, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if action_type.startswith("sheets."):
        safe_cfg = {
            k: v
            for k, v in config.items()
            if k not in ("values", "email", "phone", "full_name", "cv_text")
        }
        if "values" in config and isinstance(config["values"], list):
            safe_cfg["values_count"] = len(config["values"])
        return {
            "config": safe_cfg,
            "context_keys": sorted(str(k) for k in context.keys()),
        }
    return {
        "config": config,
        "context_sample": {k: str(v)[:150] for k, v in context.items()},
    }


def _resolve_slack_channel(token: str, channel: str) -> str:
    target = channel.lstrip("#").strip()
    if target and target[0] in ("C", "G", "D") and target[1:].isalnum():
        return target
    resp = httpx.get(
        SLACK_CHANNELS_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"types": "public_channel,private_channel", "limit": "100"},
        timeout=15.0,
    )
    data = resp.json() if resp.status_code == 200 else {}
    channels = data.get("channels") or []
    for ch in channels:
        if ch.get("name") == target and ch.get("id"):
            return str(ch["id"])
    for ch in channels:
        if ch.get("is_member") and ch.get("id"):
            return str(ch["id"])
    if channels and channels[0].get("id"):
        return str(channels[0]["id"])
    return channel


def _post_slack_message(token: str, channel: str, text: str) -> dict[str, Any]:
    resolved_channel = _resolve_slack_channel(token, channel)
    resp = httpx.post(
        SLACK_POST_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": resolved_channel, "text": text},
        timeout=15.0,
    )
    data = resp.json()
    if resp.status_code != 200 or not data.get("ok"):
        raise ActionError(f"Slack rejected the message: {data.get('error', resp.status_code)}")
    return data


class ActionDispatcher:
    """Dispatches external actions through tenant's authenticated connectors with idempotent replay guards."""

    @classmethod
    def _active_connection(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        provider: str,
        department_id: uuid.UUID | None = None,
        explicit_connection_id: uuid.UUID | None = None,
    ) -> Connection | None:
        stmt = select(Connection).where(
            Connection.tenant_id == tenant_id,
            Connection.provider == provider,
            Connection.status == ConnectionStatus.HEALTHY,
        )
        if explicit_connection_id is not None:
            stmt = stmt.where(Connection.id == explicit_connection_id)
        if department_id is not None:
            stmt = stmt.where(Connection.department_id == department_id)
        else:
            stmt = stmt.where(Connection.department_id.is_(None))
        return db.scalar(stmt.order_by(Connection.updated_at.desc()))

    @classmethod
    def _run_slack(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        config: dict[str, Any],
        context: dict[str, Any],
        department_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, str, dict[str, Any]]:
        explicit_cid = (
            uuid.UUID(str(config["connection_id"]))
            if config.get("connection_id")
            else None
        )
        conn = cls._active_connection(
            db, tenant_id, "slack", department_id=department_id, explicit_connection_id=explicit_cid
        )
        if conn is None:
            scope_label = f"department {department_id}" if department_id else "company-wide scope"
            raise ActionError(
                f"No authorized Slack connection found for {scope_label}. Cross-department fallback is prohibited."
            )
        channel = config.get("channel", "#support")
        message_text = (
            context.get("resolution")
            or context.get("draft")
            or config.get("message")
            or "OpsMind automated event alert."
        )
        data = _post_slack_message(_slack_token(conn), channel, str(message_text))
        return conn.id, str(data.get("ts") or ""), {
            "ok": True,
            "channel": data.get("channel", channel),
            "message_preview": str(message_text)[:120],
        }

    @classmethod
    def _run_email(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        config: dict[str, Any],
        context: dict[str, Any],
        department_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID | None, str, dict[str, Any]]:
        recipient = (
            context.get("to_email")
            or context.get("recipient")
            or context.get("from_email")
            or config.get("to")
        )
        if not recipient:
            raise ActionError("No email recipient found in the workflow context or step config.")
        subject = context.get("subject") or config.get("subject") or "OpsMind Resolution Response"
        body = (
            context.get("resolution")
            or context.get("draft")
            or config.get("body")
            or "Thank you for contacting us."
        )
        explicit_cid = (
            uuid.UUID(str(config["connection_id"]))
            if config.get("connection_id")
            else None
        )
        conn = cls._active_connection(
            db, tenant_id, "gmail", department_id=department_id, explicit_connection_id=explicit_cid
        )
        if explicit_cid is not None and conn is None:
            raise ActionError(
                "Specified email connection is not authorized for this workflow's department scope."
            )
        email_config = email_service.get_effective_email_config(db, tenant_id)
        if not email_config.is_configured:
            from app.services import composio_service

            if composio_service.is_composio_configured():
                if department_id is not None and conn is None:
                    raise ActionError(
                        f"No authorized Gmail connection found for department {department_id}."
                    )
                scoped_uid = (
                    f"{tenant_id}:dept:{department_id}" if department_id else str(tenant_id)
                )
                comp_res = composio_service.execute_tool(
                    user_id=scoped_uid,
                    tool_slug="GMAIL_SEND_EMAIL",
                    arguments={
                        "recipient_email": recipient,
                        "subject": subject,
                        "body": body,
                    },
                )
                return (
                    conn.id if conn else None,
                    f"composio_email_{uuid.uuid4().hex[:12]}",
                    {
                        "ok": True,
                        "to": recipient,
                        "subject": subject,
                        "composio": comp_res,
                    },
                )
            raise ActionError("Email sending requires COMPOSIO_API_KEY (Gmail) or SMTP configuration.")
        if department_id is not None and explicit_cid is not None and conn is None:
            raise ActionError("Cross-department connection access is prohibited.")
        sent, message = email_service.send_email(
            to_email=recipient,
            subject=subject,
            html_content=f"<p>{body}</p>",
            text_content=body,
            config=email_config,
        )
        if not sent:
            raise ActionError(message)
        return (
            conn.id if conn else None,
            f"email_{uuid.uuid4().hex[:12]}",
            {"ok": True, "to": recipient, "subject": subject, "status": message},
        )

    @classmethod
    def _run_google_sheets(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        action_type: str,
        config: dict[str, Any],
        context: dict[str, Any],
        department_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, str, dict[str, Any]]:
        explicit_cid = (
            uuid.UUID(str(config["connection_id"]))
            if config.get("connection_id")
            else None
        )
        conn = cls._active_connection(
            db,
            tenant_id,
            "google_sheets",
            department_id=department_id,
            explicit_connection_id=explicit_cid,
        )
        if conn is None:
            scope_label = f"department {department_id}" if department_id else "company-wide scope"
            raise ActionError(
                f"No authorized Google Sheets connection found for {scope_label}. Cross-department fallback is prohibited."
            )

        token_dict = _decrypt_connection_tokens(conn, "Google Sheets")
        tokens = TokenSet.from_dict(token_dict)
        if not tokens.access_token:
            raise ActionError("Google Sheets connection has no access token. Reconnect Google Sheets.")

        connector = _build_sheets_connector()

        async def _call(tok: TokenSet) -> dict[str, Any]:
            if action_type == "sheets.create_spreadsheet":
                title = str(config.get("title") or "").strip()
                if not title:
                    raise ActionError("sheets.create_spreadsheet requires 'title'.")
                sheet_titles = config.get("sheet_titles")
                return await connector.create_spreadsheet(
                    tok,
                    title=title,
                    sheet_titles=list(sheet_titles) if isinstance(sheet_titles, list) else None,
                )
            spreadsheet_id = str(config.get("spreadsheet_id") or "").strip()
            if not spreadsheet_id:
                raise ActionError(f"{action_type} requires 'spreadsheet_id'.")

            if action_type == "sheets.create_worksheet":
                title = str(config.get("title") or config.get("worksheet_name") or "").strip()
                if not title:
                    raise ActionError("sheets.create_worksheet requires 'title'.")
                return await connector.create_worksheet(tok, spreadsheet_id=spreadsheet_id, title=title)

            range_name = str(
                config.get("range") or config.get("range_name") or config.get("worksheet_name") or "Sheet1!A:Z"
            ).strip()
            if action_type == "sheets.read_rows":
                return await connector.read_rows(tok, spreadsheet_id=spreadsheet_id, range_name=range_name)

            if action_type == "sheets.append_row":
                values = config.get("values")
                if not isinstance(values, list) or not values:
                    raise ActionError("sheets.append_row requires a non-empty 'values' list.")
                return await connector.append_row(
                    tok,
                    spreadsheet_id=spreadsheet_id,
                    range_name=range_name,
                    values=values,
                    value_input_option=str(config.get("value_input_option") or "USER_ENTERED"),
                )

            if action_type == "sheets.update_row":
                values = config.get("values")
                if not isinstance(values, list) or not values:
                    raise ActionError("sheets.update_row requires a non-empty 'values' list.")
                if not (config.get("range") or config.get("range_name")):
                    raise ActionError("sheets.update_row requires 'range' or 'range_name'.")
                return await connector.update_row(
                    tok,
                    spreadsheet_id=spreadsheet_id,
                    range_name=range_name,
                    values=values,
                    value_input_option=str(config.get("value_input_option") or "USER_ENTERED"),
                )

            if action_type == "sheets.search_rows":
                query = str(config.get("query") or "").strip()
                if not query:
                    raise ActionError("sheets.search_rows requires 'query'.")
                col_idx = config.get("column_index")
                return await connector.search_rows(
                    tok,
                    spreadsheet_id=spreadsheet_id,
                    range_name=range_name,
                    query=query,
                    column_index=int(col_idx) if col_idx is not None else None,
                    match_exact=bool(config.get("match_exact", False)),
                )

            raise ActionError(f"Unsupported Google Sheets action: {action_type}")

        try:
            result_data = _run_async_action(_call(tokens))
        except TokenExpired as exc:
            if not tokens.refresh_token:
                raise ActionError(
                    "Google Sheets access token expired and no refresh token is stored."
                ) from exc
            refreshed = _run_async_action(connector.refresh(tokens))
            keyring = _keyring_from_settings()
            enc_ct, enc_kid = crypto.encrypt(
                refreshed.to_dict(),
                tenant_id=conn.tenant_id,
                connection_id=conn.id,
                keyring=keyring,
            )
            conn.credentials_ciphertext = enc_ct.encode("ascii")
            conn.credentials_key_id = enc_kid
            db.flush()
            result_data = _run_async_action(_call(refreshed))
        except ConnectorError as exc:
            raise ActionError(str(exc)) from exc

        ext_ref = str(
            result_data.get("updated_range")
            or result_data.get("worksheet_id")
            or result_data.get("spreadsheet_id")
            or ""
        )
        return conn.id, ext_ref, result_data

    @classmethod
    def _run_hubspot(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        action_type: str,
        config: dict[str, Any],
        department_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, str, dict[str, Any]]:
        explicit_cid = (
            uuid.UUID(str(config["connection_id"]))
            if config.get("connection_id")
            else None
        )
        conn = cls._active_connection(
            db,
            tenant_id,
            "hubspot",
            department_id=department_id,
            explicit_connection_id=explicit_cid,
        )
        if conn is None:
            scope_label = f"department {department_id}" if department_id else "company-wide scope"
            raise ActionError(
                f"No authorized HubSpot connection found for {scope_label}. Cross-department fallback is prohibited."
            )
        token_dict = _decrypt_connection_tokens(conn, "HubSpot")
        token = token_dict.get("access_token")
        if not token:
            raise ActionError("HubSpot connection has no access token. Reconnect HubSpot.")

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if action_type == "hubspot.create_ticket":
            props = {
                "subject": str(config.get("subject") or "Support Ticket"),
                "content": str(config.get("content") or config.get("description") or ""),
                "hs_pipeline_stage": str(config.get("stage") or "1"),
                "hs_ticket_priority": str(config.get("priority") or "MEDIUM").upper(),
            }
            resp = httpx.post(
                "https://api.hubapi.com/crm/v3/objects/tickets",
                headers=headers,
                json={"properties": props},
                timeout=15.0,
            )
        elif action_type == "hubspot.update_ticket":
            ticket_id = str(config.get("ticket_id") or "").strip()
            if not ticket_id:
                raise ActionError("hubspot.update_ticket requires 'ticket_id'.")
            props = {k: v for k, v in config.items() if k not in ("ticket_id", "connection_id", "idempotency_key", "provider") and v is not None}
            resp = httpx.patch(
                f"https://api.hubapi.com/crm/v3/objects/tickets/{ticket_id}",
                headers=headers,
                json={"properties": props},
                timeout=15.0,
            )
        elif action_type == "hubspot.create_contact":
            email = str(config.get("email") or "").strip()
            if not email:
                raise ActionError("hubspot.create_contact requires 'email'.")
            props = {
                "email": email,
                "firstname": str(config.get("firstname") or ""),
                "lastname": str(config.get("lastname") or ""),
                "company": str(config.get("company") or ""),
            }
            resp = httpx.post(
                "https://api.hubapi.com/crm/v3/objects/contacts",
                headers=headers,
                json={"properties": props},
                timeout=15.0,
            )
        else:
            raise ActionError(f"Unsupported HubSpot action: {action_type}")

        if resp.status_code == 429:
            raise ActionError("HubSpot rate limit exceeded (429). Retry shortly.")
        if resp.status_code >= 400:
            raise ActionError(f"HubSpot API error ({resp.status_code}).")
        data = resp.json() if resp.content else {}
        ext_id = str(data.get("id") or "")
        return conn.id, ext_id, {"ok": True, "id": ext_id, "properties": data.get("properties") or {}}

    @classmethod
    def _run_zoho_books(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        action_type: str,
        config: dict[str, Any],
        department_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, str, dict[str, Any]]:
        if action_type != "zoho_books.create_bill":
            raise ActionError(f"Unsupported Zoho Books action: {action_type}")
        explicit_cid = (
            uuid.UUID(str(config["connection_id"]))
            if config.get("connection_id")
            else None
        )
        conn = cls._active_connection(
            db,
            tenant_id,
            "zoho_books",
            department_id=department_id,
            explicit_connection_id=explicit_cid,
        )
        if conn is None:
            scope_label = f"department {department_id}" if department_id else "company-wide scope"
            raise ActionError(
                f"No authorized Zoho Books connection found for {scope_label}. Cross-department fallback is prohibited."
            )
        token_dict = _decrypt_connection_tokens(conn, "Zoho Books")
        tokens = TokenSet.from_dict(token_dict)
        if not tokens.access_token:
            raise ActionError("Zoho Books connection has no access token.")

        connector = _build_zoho_connector()
        payload = {
            "vendor_id": config.get("vendor_id"),
            "bill_number": config.get("bill_number") or config.get("invoice_number"),
            "date": config.get("date"),
            "due_date": config.get("due_date"),
            "line_items": config.get("line_items") or [
                {
                    "name": config.get("description") or "Supplier Bill",
                    "rate": float(config.get("total_amount") or 0),
                    "quantity": 1,
                }
            ],
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        async def _call(tok: TokenSet) -> dict[str, Any]:
            for attempt in range(2):
                try:
                    return await connector.create_bill(
                        tok,
                        organization_id=config.get("organization_id"),
                        bill_data=payload,
                    )
                except RateLimited as exc:
                    if attempt == 0:
                        await asyncio.sleep(min(float(exc.retry_after or 1.0), 2.0))
                        continue
                    raise

        try:
            res = _run_async_action(_call(tokens))
        except TokenExpired as exc:
            if not tokens.refresh_token:
                raise ActionError("Zoho Books access token expired and no refresh token is stored.") from exc
            refreshed = _run_async_action(connector.refresh(tokens.refresh_token))
            enc_ct, enc_kid = crypto.encrypt(
                refreshed.to_dict(),
                tenant_id=conn.tenant_id,
                connection_id=conn.id,
                keyring=_keyring_from_settings(),
            )
            conn.credentials_ciphertext = enc_ct.encode("ascii")
            conn.credentials_key_id = enc_kid
            db.flush()
            res = _run_async_action(_call(refreshed))
        except (ConnectorError, RateLimited) as exc:
            raise ActionError(str(exc)) from exc

        bill_obj = res.get("bill") if isinstance(res.get("bill"), dict) else res
        ext_ref = str(
            (bill_obj or {}).get("bill_id")
            or (bill_obj or {}).get("bill_number")
            or payload.get("bill_number")
            or ""
        )
        return conn.id, ext_ref, {"ok": True, **res}

    @classmethod
    def _run_google_calendar(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        action_type: str,
        config: dict[str, Any],
        department_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, str, dict[str, Any]]:
        if action_type != "google_calendar.create_event":
            raise ActionError(f"Unsupported Google Calendar action: {action_type}")
        conn = cls._active_connection(db, tenant_id, "google_calendar", department_id=department_id)
        if conn is None:
            scope_label = f"department {department_id}" if department_id else "company-wide scope"
            raise ActionError(
                f"No authorized Google Calendar connection found for {scope_label}. Cross-department fallback is prohibited."
            )
        token_dict = _decrypt_connection_tokens(conn, "Google Calendar")
        access_token = token_dict.get("access_token")
        if not access_token:
            raise ActionError("Google Calendar connection has no access token.")
        calendar_id = str(config.get("calendar_id") or "primary")
        summary = str(config.get("summary") or config.get("title") or "OpsMind Meeting")
        start = config.get("start") or {"dateTime": config.get("start_time") or "2026-11-20T10:00:00Z"}
        end = config.get("end") or {"dateTime": config.get("end_time") or "2026-11-20T10:30:00Z"}
        body: dict[str, Any] = {"summary": summary, "start": start, "end": end}
        if config.get("description"):
            body["description"] = str(config["description"])
        if config.get("attendees"):
            body["attendees"] = config["attendees"]

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {access_token}"},
                json=body,
            )
        if resp.status_code >= 400:
            raise ActionError(f"Google Calendar API error ({resp.status_code}): {resp.text[:200]}")
        data = resp.json() if resp.content else {}
        ext_id = str(data.get("id") or f"gcal_{uuid.uuid4().hex[:10]}")
        return conn.id, ext_id, {"ok": True, "id": ext_id, "htmlLink": data.get("htmlLink")}

    @classmethod
    def _run_google_drive(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        action_type: str,
        config: dict[str, Any],
        department_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, str, dict[str, Any]]:
        if action_type != "google_drive.upload_file":
            raise ActionError(f"Unsupported Google Drive action: {action_type}")
        conn = cls._active_connection(db, tenant_id, "google_drive", department_id=department_id)
        if conn is None:
            scope_label = f"department {department_id}" if department_id else "company-wide scope"
            raise ActionError(
                f"No authorized Google Drive connection found for {scope_label}. Cross-department fallback is prohibited."
            )
        token_dict = _decrypt_connection_tokens(conn, "Google Drive")
        access_token = token_dict.get("access_token")
        if not access_token:
            raise ActionError("Google Drive connection has no access token.")
        name = str(config.get("name") or config.get("filename") or "document.txt")
        mime_type = str(config.get("mime_type") or "text/plain")
        content = str(config.get("content") or "")
        metadata: dict[str, Any] = {"name": name, "mimeType": mime_type}
        if config.get("folder_id"):
            metadata["parents"] = [str(config["folder_id"])]

        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                headers={"Authorization": f"Bearer {access_token}"},
                files={
                    "metadata": ("metadata", json.dumps(metadata), "application/json"),
                    "file": (name, content.encode("utf-8"), mime_type),
                },
            )
        if resp.status_code >= 400:
            raise ActionError(f"Google Drive API error ({resp.status_code}): {resp.text[:200]}")
        data = resp.json() if resp.content else {}
        ext_id = str(data.get("id") or f"gdrive_{uuid.uuid4().hex[:10]}")
        return conn.id, ext_id, {"ok": True, "id": ext_id, "name": data.get("name") or name}

    _STRIPE_MONEY_MOVING_ACTIONS = frozenset({
        "stripe.create_charge",
        "stripe.create_refund",
        "stripe.create_payout",
        "stripe.create_transfer",
    })

    _APPROVED_COMPOSIO_TOOLKIT_PREFIXES = frozenset({
        "GMAIL_",
        "GOOGLEDRIVE_",
        "GOOGLECALENDAR_",
        "GOOGLESHEETS_",
        "SLACK_",
        "HUBSPOT_",
        "STRIPE_",
    })

    _STRIPE_MONEY_MOVING_KEYWORDS = (
        "CHARGE",
        "REFUND",
        "PAYOUT",
        "TRANSFER",
        "PAYMENT",
        "PAY_INVOICE",
        "CAPTURE",
    )

    _STRIPE_ENDPOINTS = {
        "stripe.create_charge": "charges",
        "stripe.create_refund": "refunds",
        "stripe.create_payout": "payouts",
        "stripe.create_transfer": "transfers",
    }

    @classmethod
    def is_composio_tool_allowed(cls, tool_slug: str) -> bool:
        upper = (tool_slug or "").strip().upper()
        return any(upper.startswith(prefix) for prefix in cls._APPROVED_COMPOSIO_TOOLKIT_PREFIXES)

    @classmethod
    def is_composio_money_moving(cls, tool_slug: str) -> bool:
        upper = (tool_slug or "").strip().upper()
        if not upper.startswith("STRIPE_"):
            return False
        return any(kw in upper for kw in cls._STRIPE_MONEY_MOVING_KEYWORDS)

    @classmethod
    def _has_human_approval(cls, db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> bool:
        approved_review = db.scalar(
            select(ReviewTask.id).where(
                ReviewTask.tenant_id == tenant_id,
                ReviewTask.run_id == run_id,
                ReviewTask.status == ReviewTaskStatus.APPROVED,
            )
        )
        if approved_review is not None:
            return True
        approved_wf = db.scalar(
            select(WorkflowApproval.id).where(
                WorkflowApproval.tenant_id == tenant_id,
                WorkflowApproval.run_id == run_id,
                WorkflowApproval.status == WorkflowApprovalStatus.APPROVED,
            )
        )
        return approved_wf is not None

    @classmethod
    def _run_stripe(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        action_type: str,
        config: dict[str, Any],
        department_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, str, dict[str, Any]]:
        if action_type not in cls._STRIPE_ENDPOINTS:
            raise ActionError(f"Unsupported Stripe action: {action_type}")
        if action_type in cls._STRIPE_MONEY_MOVING_ACTIONS and not cls._has_human_approval(
            db, tenant_id, run_id
        ):
            raise ActionError(
                "Money-moving Stripe action requires an approved ReviewTask or WorkflowApproval and cannot run from an Agent/AI-only path."
            )

        conn = cls._active_connection(db, tenant_id, "stripe", department_id=department_id)
        if conn is None:
            scope_label = f"department {department_id}" if department_id else "company-wide scope"
            raise ActionError(
                f"No authorized Stripe connection found for {scope_label}. Cross-department fallback is prohibited."
            )
        token_dict = _decrypt_connection_tokens(conn, "Stripe")
        access_token = token_dict.get("access_token")
        if not access_token:
            raise ActionError("Stripe connection has no access token.")

        endpoint = cls._STRIPE_ENDPOINTS[action_type]
        form_data = {
            k: str(v)
            for k, v in config.items()
            if k not in ("idempotency_key", "provider", "connection_id") and v is not None
        }
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"https://api.stripe.com/v1/{endpoint}",
                headers={"Authorization": f"Bearer {access_token}"},
                data=form_data,
            )
        if resp.status_code >= 400:
            raise ActionError(f"Stripe API error ({resp.status_code}): {resp.text[:200]}")
        data = resp.json() if resp.content else {}
        ext_id = str(data.get("id") or f"stripe_{uuid.uuid4().hex[:10]}")
        return conn.id, ext_id, {"ok": True, "id": ext_id, "status": data.get("status")}

    @classmethod
    def dispatch(
        cls,
        db: Session,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        run_step_id: uuid.UUID | None,
        action_type: str,
        config: dict[str, Any],
        context: dict[str, Any],
        department_id: uuid.UUID | None = None,
    ) -> ActionResult:
        """Executes an action node and records its audit log idempotently."""
        idempotency_key = (
            config.get("idempotency_key")
            or f"{run_id}:{run_step_id or 'step'}:{action_type}"
        )
        now = datetime.now(UTC)

        existing_log = db.scalar(
            select(ActionLog).where(
                ActionLog.tenant_id == tenant_id,
                ActionLog.idempotency_key == idempotency_key,
            )
        )

        if existing_log and existing_log.status == ActionStatus.SUCCEEDED:
            logger.info("Action already executed idempotently: key=%s", idempotency_key)
            return ActionResult(
                success=True,
                action_type=action_type,
                external_ref=existing_log.external_ref,
                response_data=existing_log.response,
                idempotent_replay=True,
            )

        provider = config.get("provider") or (
            "google_sheets"
            if action_type.startswith("sheets.")
            else (
                "hubspot"
                if action_type.startswith("hubspot.")
                else (
                    "zoho_books"
                    if action_type.startswith("zoho_books.")
                    else (
                        "google_calendar"
                        if action_type.startswith("google_calendar.")
                        else (
                            "google_drive"
                            if action_type.startswith("google_drive.")
                            else (
                                "stripe"
                                if action_type.startswith("stripe.")
                                else ("slack" if "slack" in action_type else "email")
                            )
                        )
                    )
                )
            )
        )

        connection_id: uuid.UUID | None = None
        external_ref: str | None = None
        response_payload: dict[str, Any] = {}
        error_msg: str | None = None
        success = True

        try:
            if provider == "slack":
                connection_id, external_ref, response_payload = cls._run_slack(
                    db, tenant_id, config, context, department_id=department_id
                )
            elif provider in ("google_sheets", "sheets"):
                connection_id, external_ref, response_payload = cls._run_google_sheets(
                    db, tenant_id, action_type, config, context, department_id=department_id
                )
            elif provider == "hubspot":
                connection_id, external_ref, response_payload = cls._run_hubspot(
                    db, tenant_id, action_type, config, department_id=department_id
                )
            elif provider == "zoho_books":
                connection_id, external_ref, response_payload = cls._run_zoho_books(
                    db, tenant_id, action_type, config, department_id=department_id
                )
            elif provider == "google_calendar":
                connection_id, external_ref, response_payload = cls._run_google_calendar(
                    db, tenant_id, action_type, config, department_id=department_id
                )
            elif provider == "google_drive":
                connection_id, external_ref, response_payload = cls._run_google_drive(
                    db, tenant_id, action_type, config, department_id=department_id
                )
            elif provider == "stripe":
                connection_id, external_ref, response_payload = cls._run_stripe(
                    db, tenant_id, run_id, action_type, config, department_id=department_id
                )
            elif provider in ("email", "gmail"):
                connection_id, external_ref, response_payload = cls._run_email(
                    db, tenant_id, config, context, department_id=department_id
                )
            else:
                raise ActionError(f"Unsupported action type: {action_type}")
        except Exception as exc:
            success = False
            error_msg = str(exc)
            logger.error("Failed to execute action %s: %s", action_type, exc)

        attempts = 1
        if existing_log:
            existing_log.status = ActionStatus.SUCCEEDED if success else ActionStatus.FAILED
            existing_log.attempts += 1
            existing_log.response = response_payload
            existing_log.external_ref = external_ref or existing_log.external_ref
            existing_log.connection_id = connection_id or existing_log.connection_id
            existing_log.error = error_msg
            existing_log.executed_at = now
            attempts = existing_log.attempts
            db.flush()
        else:
            db.add(
                ActionLog(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    run_step_id=run_step_id,
                    connection_id=connection_id,
                    action_type=action_type,
                    idempotency_key=idempotency_key,
                    status=ActionStatus.SUCCEEDED if success else ActionStatus.FAILED,
                    request=_sanitize_request_for_log(action_type, config, context),
                    response=response_payload,
                    external_ref=external_ref,
                    attempts=attempts,
                    error=error_msg,
                    executed_at=now,
                )
            )
            db.flush()

        return ActionResult(
            success=success,
            action_type=action_type,
            external_ref=external_ref,
            response_data=response_payload,
            error=error_msg,
            idempotent_replay=False,
        )

    @classmethod
    def dispatch_composio(
        cls,
        db: Session,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        department_id: uuid.UUID | None,
        tool_slug: str,
        arguments: dict[str, Any],
        run_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute an allowlisted Composio tool with money-moving approval gate, idempotency, and audit logging."""
        from app.db.models.audit_log import AuditLog
        from app.services import composio_service

        clean_slug = (tool_slug or "").strip()
        if not cls.is_composio_tool_allowed(clean_slug):
            raise ActionError(
                f"Composio tool '{clean_slug}' is not in the approved provider allowlist."
            )

        if cls.is_composio_money_moving(clean_slug):
            if run_id is None or not cls._has_human_approval(db, tenant_id, run_id):
                raise ActionError(
                    "Money-moving Stripe tool requires an approved ReviewTask or WorkflowApproval for run_id."
                )

        scope_user_id = f"{tenant_id}:dept:{department_id}" if department_id else str(tenant_id)
        action_type = f"composio.{clean_slug.lower()}"
        now = datetime.now(UTC)

        existing_log: ActionLog | None = None
        eff_idem_key = idempotency_key or (f"{run_id}:composio:{clean_slug}" if run_id else None)
        if run_id is not None and eff_idem_key:
            existing_log = db.scalar(
                select(ActionLog).where(
                    ActionLog.tenant_id == tenant_id,
                    ActionLog.idempotency_key == eff_idem_key,
                )
            )
            if existing_log and existing_log.status == ActionStatus.SUCCEEDED:
                return dict(existing_log.response or {"idempotent_replay": True, "tool_slug": clean_slug})

        try:
            result = composio_service.execute_tool(
                user_id=scope_user_id,
                tool_slug=clean_slug,
                arguments=arguments,
            )
        except Exception as exc:
            if run_id is not None and eff_idem_key:
                if existing_log:
                    existing_log.status = ActionStatus.FAILED
                    existing_log.attempts += 1
                    existing_log.error = str(exc)
                    existing_log.executed_at = now
                else:
                    db.add(
                        ActionLog(
                            tenant_id=tenant_id,
                            run_id=run_id,
                            run_step_id=None,
                            connection_id=None,
                            action_type=action_type,
                            idempotency_key=eff_idem_key,
                            status=ActionStatus.FAILED,
                            request={"tool_slug": clean_slug, "department_id": str(department_id) if department_id else None},
                            response={},
                            attempts=1,
                            error=str(exc),
                            executed_at=now,
                        )
                    )
                db.flush()
            raise

        if run_id is not None and eff_idem_key:
            if existing_log:
                existing_log.status = ActionStatus.SUCCEEDED
                existing_log.attempts += 1
                existing_log.response = result
                existing_log.error = None
                existing_log.executed_at = now
            else:
                db.add(
                    ActionLog(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        run_step_id=None,
                        connection_id=None,
                        action_type=action_type,
                        idempotency_key=eff_idem_key,
                        status=ActionStatus.SUCCEEDED,
                        request={"tool_slug": clean_slug, "department_id": str(department_id) if department_id else None},
                        response=result,
                        attempts=1,
                        executed_at=now,
                    )
                )
        else:
            db.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_user_id=user_id,
                    action="composio.tool_executed",
                    entity_type="connection",
                    entity_id=str(department_id) if department_id else None,
                    details={"tool_slug": clean_slug, "department_id": str(department_id) if department_id else None},
                )
            )
        db.flush()
        return result
