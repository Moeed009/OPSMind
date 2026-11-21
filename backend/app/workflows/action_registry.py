"""Action registry mapping workflow action keys to department-scoped connector implementations."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.workflows.actions import ActionDispatcher, ActionResult


@dataclass(frozen=True)
class ActionSpec:
    key: str
    provider: str
    available: bool
    dispatcher_action: str | None = None


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "gmail.send_reply": ActionSpec(
        key="gmail.send_reply",
        provider="gmail",
        available=True,
        dispatcher_action="gmail.send_message",
    ),
    "gmail.send_email": ActionSpec(
        key="gmail.send_email",
        provider="gmail",
        available=True,
        dispatcher_action="gmail.send_message",
    ),
    "slack.post_message": ActionSpec(
        key="slack.post_message",
        provider="slack",
        available=True,
        dispatcher_action="slack.post_message",
    ),
    "hubspot.create_ticket": ActionSpec(
        key="hubspot.create_ticket",
        provider="hubspot",
        available=True,
        dispatcher_action="hubspot.create_ticket",
    ),
    "hubspot.update_ticket": ActionSpec(
        key="hubspot.update_ticket",
        provider="hubspot",
        available=True,
        dispatcher_action="hubspot.update_ticket",
    ),
    "hubspot.create_contact": ActionSpec(
        key="hubspot.create_contact",
        provider="hubspot",
        available=True,
        dispatcher_action="hubspot.create_contact",
    ),
    "zoho_books.create_bill": ActionSpec(
        key="zoho_books.create_bill",
        provider="zoho_books",
        available=True,
        dispatcher_action="zoho_books.create_bill",
    ),
    "google_calendar.create_event": ActionSpec(
        key="google_calendar.create_event",
        provider="google_calendar",
        available=True,
        dispatcher_action="google_calendar.create_event",
    ),
    "google_drive.upload_file": ActionSpec(
        key="google_drive.upload_file",
        provider="google_drive",
        available=True,
        dispatcher_action="google_drive.upload_file",
    ),
    "stripe.create_refund": ActionSpec(
        key="stripe.create_refund",
        provider="stripe",
        available=True,
        dispatcher_action="stripe.create_refund",
    ),
    "stripe.create_charge": ActionSpec(
        key="stripe.create_charge",
        provider="stripe",
        available=True,
        dispatcher_action="stripe.create_charge",
    ),
    "stripe.create_payout": ActionSpec(
        key="stripe.create_payout",
        provider="stripe",
        available=True,
        dispatcher_action="stripe.create_payout",
    ),
    "stripe.create_transfer": ActionSpec(
        key="stripe.create_transfer",
        provider="stripe",
        available=True,
        dispatcher_action="stripe.create_transfer",
    ),
    "sheets.create_spreadsheet": ActionSpec(
        key="sheets.create_spreadsheet",
        provider="google_sheets",
        available=True,
        dispatcher_action="sheets.create_spreadsheet",
    ),
    "sheets.create_worksheet": ActionSpec(
        key="sheets.create_worksheet",
        provider="google_sheets",
        available=True,
        dispatcher_action="sheets.create_worksheet",
    ),
    "sheets.read_rows": ActionSpec(
        key="sheets.read_rows",
        provider="google_sheets",
        available=True,
        dispatcher_action="sheets.read_rows",
    ),
    "sheets.append_row": ActionSpec(
        key="sheets.append_row",
        provider="google_sheets",
        available=True,
        dispatcher_action="sheets.append_row",
    ),
    "sheets.update_row": ActionSpec(
        key="sheets.update_row",
        provider="google_sheets",
        available=True,
        dispatcher_action="sheets.update_row",
    ),
    "sheets.search_rows": ActionSpec(
        key="sheets.search_rows",
        provider="google_sheets",
        available=True,
        dispatcher_action="sheets.search_rows",
    ),
}


def is_action_available(action_key: str) -> bool:
    spec = ACTION_REGISTRY.get(action_key)
    return bool(spec and spec.available)


def execute_registered_action(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID | None,
    run_id: uuid.UUID,
    run_step_id: uuid.UUID | None,
    action_key: str,
    params: dict[str, Any],
    context: dict[str, Any],
    node_id: str | None = None,
    idempotency_run_id: uuid.UUID | None = None,
) -> ActionResult:
    spec = ACTION_REGISTRY.get(action_key)
    if spec is None or not spec.available or spec.dispatcher_action is None:
        return ActionResult(
            success=False,
            action_type=action_key,
            error=f"Action '{action_key}' is not_available in this deployment.",
        )

    effective_params = dict(params)
    if "idempotency_key" not in effective_params:
        idem_rid = idempotency_run_id or run_id
        effective_params["idempotency_key"] = f"dept:{idem_rid}:{node_id or run_step_id or 'step'}:{action_key}"

    return ActionDispatcher.dispatch(
        db=db,
        tenant_id=tenant_id,
        run_id=run_id,
        run_step_id=run_step_id,
        action_type=spec.dispatcher_action,
        config=effective_params,
        context=context,
        department_id=department_id,
    )
