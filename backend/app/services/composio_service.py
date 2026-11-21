
from __future__ import annotations

import logging
import os
from typing import Any

from composio import Composio

from app.core.config import get_settings

logger = logging.getLogger("opsmind.composio")

PROVIDER_TO_TOOLKIT: dict[str, str] = {
    "gmail": "gmail",
    "google_drive": "googledrive",
    "google_calendar": "googlecalendar",
    "google_sheets": "googlesheets",
    "slack": "slack",
    "hubspot": "hubspot",
    "stripe": "stripe",
}

_SESSION_CACHE: dict[str, str] = {}


def is_composio_configured() -> bool:
    """Checks whether COMPOSIO_API_KEY is present in environment or settings."""
    settings = get_settings()
    key = os.environ.get("COMPOSIO_API_KEY") or settings.composio_api_key
    if key and key.strip() and not os.environ.get("COMPOSIO_API_KEY"):
        os.environ["COMPOSIO_API_KEY"] = key.strip()
    return bool(key and key.strip())


def get_composio_client() -> Composio:
    """Returns a Composio SDK instance reading COMPOSIO_API_KEY from environment."""
    is_composio_configured()
    return Composio()


def get_or_create_session(user_id: str):
    """Creates or resumes a Composio session for the given tenant/user identifier."""
    composio = get_composio_client()
    cached_session_id = _SESSION_CACHE.get(user_id)
    if cached_session_id:
        try:
            return composio.use(cached_session_id)
        except Exception:
            _SESSION_CACHE.pop(user_id, None)
    session = composio.create(user_id=user_id)
    _SESSION_CACHE[user_id] = session.session_id
    return session


def toolkit_for_provider(provider: str) -> str:
    return PROVIDER_TO_TOOLKIT.get(provider, provider)


def authorize_provider(user_id: str, provider: str, callback_url: str | None = None) -> dict[str, str]:
    """Initiates Composio managed authorization for a toolkit and returns a Connect Link."""
    toolkit = toolkit_for_provider(provider)
    session = get_or_create_session(user_id)
    conn_req = session.authorize(toolkit, callback_url=callback_url)
    return {
        "session_id": session.session_id,
        "connected_account_id": str(conn_req.id or ""),
        "authorize_url": str(conn_req.redirect_url or ""),
        "toolkit": toolkit,
    }


def get_toolkit_connection_state(user_id: str, provider: str) -> dict[str, Any]:
    """Checks whether a toolkit is active/connected in the user's Composio session."""
    toolkit = toolkit_for_provider(provider)
    session = get_or_create_session(user_id)
    details = session.toolkits(toolkits=[toolkit])
    for item in details.items:
        if item.slug.lower() == toolkit.lower():
            conn = item.connection
            acc = conn.connected_account if conn else None
            return {
                "slug": item.slug,
                "name": item.name,
                "is_active": bool(conn and conn.is_active),
                "connected_account_id": acc.id if acc else None,
                "status": acc.status if acc else None,
            }
    return {
        "slug": toolkit,
        "name": provider,
        "is_active": False,
        "connected_account_id": None,
        "status": None,
    }


def search_tools(user_id: str, query: str) -> dict[str, Any]:
    """Discovers tools in the user's Composio session by semantic query."""
    session = get_or_create_session(user_id)
    res = session.search(query=query)
    return res.model_dump(mode="json") if hasattr(res, "model_dump") else {"result": str(res)}


def execute_tool(user_id: str, tool_slug: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Executes a tool within the user's Composio session and returns the structured response."""
    session = get_or_create_session(user_id)
    res = session.execute(tool_slug, arguments=arguments or {})
    dumped = res.model_dump(mode="json") if hasattr(res, "model_dump") else {"raw": str(res)}
    return {
        "session_id": session.session_id,
        "tool_slug": tool_slug,
        "result_type": getattr(res, "result_type", dumped.get("result_type")),
        "response": dumped,
    }


def fetch_provider_events(user_id: str, provider: str) -> list[dict[str, Any]]:
    """Fetches recent inbound events for a provider via the user's Composio session."""
    if provider == "gmail":
        res = execute_tool(
            user_id=user_id,
            tool_slug="GMAIL_FETCH_EMAILS",
            arguments={"max_results": 10},
        )
        data = (res.get("response") or {}).get("data") or {}
        messages = data.get("messages") or []
        events: list[dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            ext_id = str(msg.get("messageId") or msg.get("id") or f"gmail_{idx}")
            events.append(
                {
                    "external_id": ext_id,
                    "event_type": "email.received",
                    "payload": {
                        "subject": msg.get("subject") or "Inbound Email",
                        "message": msg.get("messageText") or msg.get("snippet") or "",
                        "from_email": msg.get("sender") or msg.get("from"),
                        "thread_id": msg.get("threadId"),
                    },
                }
            )
        return events
    if provider == "google_drive":
        res = execute_tool(
            user_id=user_id,
            tool_slug="GOOGLEDRIVE_FIND_FILE",
            arguments={"page_size": 10},
        )
        data = (res.get("response") or {}).get("data") or {}
        files = data.get("files") or []
        return [
            {
                "external_id": str(f.get("id") or f"drive_{i}"),
                "event_type": "drive.file_created",
                "payload": {
                    "subject": f.get("name") or "Drive Document",
                    "message": f"Uploaded file: {f.get('name', 'Document')} ({f.get('mimeType', '')})",
                },
            }
            for i, f in enumerate(files)
            if isinstance(f, dict)
        ]
    return []

