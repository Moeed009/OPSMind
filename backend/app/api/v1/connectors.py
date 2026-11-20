"""Connections API: OAuth connect flow, listing, health, manual poll and revoke."""
import logging
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.api.deps import TenantContext, TenantCtx
from app.api.rbac import require_permission
from app.connectors import crypto
from app.connectors.base import ConnectionStatus as ConnectorConnectionStatus
from app.connectors.errors import (
    AccountAlreadyBound,
    ConnectionRevoked,
    ConnectorError,
    Forbidden,
    InvalidOAuthState,
    NotFound,
    PollInProgress,
    UnknownProvider,
)
from app.connectors.service import (
    CONNECTIONS_MANAGE,
    CONNECTIONS_READ,
    Actor,
    ConnectionService,
    ConnectionView,
)
from app.core.config import get_settings
from app.core.permissions import Permission, has_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["connections"])

_SERVICE_PERMISSIONS = {
    Permission.CONNECTIONS_READ: CONNECTIONS_READ,
    Permission.CONNECTIONS_MANAGE: CONNECTIONS_MANAGE,
}

_ERROR_STATUS: dict[type[ConnectorError], int] = {
    Forbidden: status.HTTP_403_FORBIDDEN,
    NotFound: status.HTTP_404_NOT_FOUND,
    UnknownProvider: status.HTTP_404_NOT_FOUND,
    InvalidOAuthState: status.HTTP_400_BAD_REQUEST,
    AccountAlreadyBound: status.HTTP_409_CONFLICT,
    PollInProgress: status.HTTP_409_CONFLICT,
    ConnectionRevoked: status.HTTP_409_CONFLICT,
    ConnectorError: status.HTTP_400_BAD_REQUEST,
}

class DirectConnectRequest(BaseModel):
    provider: str
    external_account_id: str = Field(min_length=1)
    credentials: dict[str, Any] = Field(default_factory=dict)
    department_id: str | None = None


class ComposioExecuteRequest(BaseModel):
    tool_slug: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    department_id: str | None = None
    run_id: str | None = None
    idempotency_key: str | None = None

def _audit(action: str, tenant_id: str, user_id: str | None, payload: dict) -> None:
    logger.info("audit action=%s tenant=%s user=%s payload=%s", action, tenant_id, user_id, payload)
    try:
        import json
        import uuid

        from sqlalchemy import text

        from app.db.rls import set_rls_context
        from app.db.session import get_session_factory

        tid = uuid.UUID(str(tenant_id))
        uid = uuid.UUID(str(user_id)) if user_id else None
        factory = get_session_factory()
        with factory() as db, db.begin():
            set_rls_context(db, user_id=uid, tenant_id=tid)
            db.execute(
                text(
                    "INSERT INTO audit_logs (id, tenant_id, actor_user_id, action, entity_type, entity_id, details) "
                    "VALUES (:id, :tenant_id, :actor_user_id, :action, 'connection', :entity_id, CAST(:details AS jsonb))"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": str(tid),
                    "actor_user_id": str(uid) if uid else None,
                    "action": action,
                    "entity_id": payload.get("connection_id"),
                    "details": json.dumps(payload),
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist audit log to DB: %s", exc)

@lru_cache
def _build_service() -> ConnectionService:
    """Build the process-wide ConnectionService from settings (raises RuntimeError if unconfigured)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.connectors.sql_repo import SqlConnectionRepo, SqlOAuthStateStore

    settings = get_settings()
    if not settings.app_database_url:
        raise RuntimeError("APP_DATABASE_URL is not configured")
    if not settings.oauth_state_secret:
        raise RuntimeError("OAUTH_STATE_SECRET is not configured")

    keyring = crypto.KeyRing.from_env(
        {
            "CONNECTOR_ENCRYPTION_KEYS": settings.connector_encryption_keys,
            "CONNECTOR_ENCRYPTION_CURRENT_KEY_ID": settings.connector_encryption_current_key_id,
        }
    )
    engine = create_async_engine(
        settings.app_database_url,
        connect_args={"prepare_threshold": None},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def connector_factory(provider: str):
        from app.connectors.httpx_client import HttpxClient
        http = HttpxClient()

        if provider == "gmail":
            from app.connectors.gmail.connector import GmailConnector
            return GmailConnector(
                http,
                settings.google_client_id or "composio_managed",
                settings.google_client_secret or "composio_managed",
            )
        elif provider == "google_drive":
            from app.connectors.google_drive.connector import GoogleDriveConnector
            return GoogleDriveConnector(http, "composio_managed", "composio_managed")
        elif provider == "google_calendar":
            from app.connectors.google_calendar.connector import GoogleCalendarConnector
            return GoogleCalendarConnector(http, "composio_managed", "composio_managed")
        elif provider == "google_sheets":
            from app.connectors.google_sheets.connector import GoogleSheetsConnector
            return GoogleSheetsConnector(
                http,
                settings.google_client_id or "placeholder_client_id",
                settings.google_client_secret or "placeholder_secret",
            )
        elif provider == "slack":
            from app.connectors.slack.connector import SlackConnector
            return SlackConnector(
                http,
                settings.slack_client_id or "placeholder_client_id",
                settings.slack_client_secret or "placeholder_secret",
            )
        elif provider == "hubspot":
            from app.connectors.hubspot.connector import HubSpotConnector
            return HubSpotConnector(
                http,
                settings.hubspot_client_id or "placeholder_client_id",
                settings.hubspot_client_secret or "placeholder_secret",
            )
        elif provider == "stripe":
            from app.connectors.stripe.connector import StripeConnector
            return StripeConnector(
                http,
                settings.stripe_client_id or "placeholder_client_id",
                settings.stripe_client_secret or "placeholder_secret",
            )
        elif provider == "zoho_books":
            from app.connectors.zoho_books.connector import ZohoBooksConnector
            return ZohoBooksConnector(
                http,
                settings.zoho_client_id or "placeholder_client_id",
                settings.zoho_client_secret or "placeholder_secret",
            )
        raise UnknownProvider(f"provider '{provider}' is not available")

    return ConnectionService(
        repo=SqlConnectionRepo(session_factory),
        state_store=SqlOAuthStateStore(session_factory),
        keyring=keyring,
        connector_factory=connector_factory,
        state_secret=settings.oauth_state_secret,
        redirect_uri=f"{settings.oauth_redirect_base_url.rstrip('/')}{settings.api_prefix}"
        "/connections/oauth/callback",
        audit=_audit,
    )

def get_connection_service() -> ConnectionService:
    try:
        return _build_service()
    except (RuntimeError, crypto.CryptoError) as exc:
        get_settings.cache_clear()
        _build_service.cache_clear()
        try:
            return _build_service()
        except (RuntimeError, crypto.CryptoError):
            pass
        logger.error("Connector service is not configured: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Connectors are not configured"
        ) from None

def _actor(tc: TenantContext, *, force_manage: bool = False) -> Actor:
    permissions = frozenset(
        name for perm, name in _SERVICE_PERMISSIONS.items() if has_permission(tc.role, perm)
    )
    if force_manage:
        permissions = permissions | frozenset({CONNECTIONS_READ, CONNECTIONS_MANAGE})
    return Actor(tenant_id=str(tc.tenant_id), user_id=str(tc.user.id), permissions=permissions)


def _require_dept_connection_permission(
    tc: TenantContext,
    department_id: str | None,
    permission: Permission,
    *,
    require_active: bool = False,
) -> Actor:
    import uuid

    from app.services import department as department_service

    if department_id:
        try:
            dept_uuid = uuid.UUID(str(department_id))
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "department_id must be a valid UUID"
            ) from exc
        department_service.require_department_permission(
            tc.db,
            tenant_id=tc.tenant_id,
            user_id=tc.user.id,
            tenant_role=tc.role,
            department_id=dept_uuid,
            permission=permission,
            require_active=require_active,
        )
        return _actor(tc, force_manage=(permission == Permission.CONNECTIONS_MANAGE))
    if not has_permission(tc.role, permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role does not allow this action")
    return _actor(tc)


def _load_connection_dept_id(tc: TenantContext, connection_id: str) -> str | None:
    import uuid

    from sqlalchemy import select

    from app.db.models.connection import Connection

    try:
        cid = uuid.UUID(str(connection_id))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found") from exc
    conn = tc.db.scalar(
        select(Connection).where(Connection.tenant_id == tc.tenant_id, Connection.id == cid)
    )
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found")
    return str(conn.department_id) if conn.department_id else None

def read_actor(
    tc: Annotated[TenantContext, Depends(require_permission(Permission.CONNECTIONS_READ))],
) -> Actor:
    return _actor(tc)

def manage_actor(
    tc: Annotated[TenantContext, Depends(require_permission(Permission.CONNECTIONS_MANAGE))],
) -> Actor:
    return _actor(tc)

ReadActor = Annotated[Actor, Depends(read_actor)]
ManageActor = Annotated[Actor, Depends(manage_actor)]
Service = Annotated[ConnectionService, Depends(get_connection_service)]

def _http_error(exc: ConnectorError) -> HTTPException:
    for error_type, status_code in _ERROR_STATUS.items():
        if isinstance(exc, error_type):
            return HTTPException(status_code, str(exc))
    logger.exception("Unhandled connector error", exc_info=exc)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "Connector request failed")

PROVIDER_ENV_KEYS: dict[str, tuple[str, str]] = {
    "slack": ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"),
    "hubspot": ("HUBSPOT_CLIENT_ID", "HUBSPOT_CLIENT_SECRET"),
    "stripe": ("STRIPE_CLIENT_ID", "STRIPE_CLIENT_SECRET"),
    "google_sheets": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    "zoho_books": ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET"),
}

def _composio_scope_user_id(tenant_id: str, department_id: str | None = None) -> str:
    return f"{tenant_id}:dept:{department_id}" if department_id else str(tenant_id)


@router.get("", response_model=list[ConnectionView])
async def list_connections(
    tc: Annotated[TenantContext, Depends(require_permission(Permission.CONNECTIONS_READ))],
    service: Service,
    department_id: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
) -> list[ConnectionView]:
    import uuid

    from app.services import department as department_service

    actor = _actor(tc)
    try:
        items = await service.list(actor)
    except ConnectorError as exc:
        raise _http_error(exc) from exc

    if department_id:
        dept_uuid = uuid.UUID(str(department_id))
        department_service.check_department_access(
            tc.db,
            tenant_id=tc.tenant_id,
            user_id=tc.user.id,
            role=tc.role,
            department_id=dept_uuid,
            require_write=False,
        )
        return [c for c in items if c.department_id == str(dept_uuid)]

    if not department_service.is_company_admin(tc.role):
        assigned = {
            str(d)
            for d in department_service.get_assigned_department_ids(tc.db, tc.tenant_id, tc.user.id)
        }
        items = [c for c in items if c.department_id is None or c.department_id in assigned]

    if scope == "company":
        items = [c for c in items if c.department_id is None]
    elif scope == "department":
        items = [c for c in items if c.department_id is not None]
    return items


@router.post("/oauth/start/{provider}")
async def start_oauth(
    provider: str,
    tc: TenantCtx,
    service: Service,
    department_id: Annotated[str | None, Query()] = None,
) -> dict[str, str]:
    import urllib.parse

    from app.services import composio_service

    if not department_id or not department_id.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "department_id is required for new connections",
        )
    actor = _require_dept_connection_permission(
        tc, department_id, Permission.CONNECTIONS_MANAGE, require_active=True
    )

    settings = get_settings()
    if composio_service.is_composio_configured():
        try:
            signed_state = await service.issue_composio_state(
                actor, provider, department_id=department_id
            )
        except ConnectorError as exc:
            raise _http_error(exc) from exc
        callback_url = (
            f"{settings.oauth_redirect_base_url.rstrip('/')}{settings.api_prefix}"
            f"/connections/oauth/callback?composio=1&state={urllib.parse.quote(signed_state)}"
        )
        try:
            res = composio_service.authorize_provider(
                user_id=_composio_scope_user_id(actor.tenant_id, department_id),
                provider=provider,
                callback_url=callback_url,
            )
            return {"authorize_url": res["authorize_url"]}
        except Exception as exc:
            logger.exception("Composio session authorize failed for %s: %s", provider, exc)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Composio authorization failed for {provider}: {exc}",
            ) from exc

    required = PROVIDER_ENV_KEYS.get(provider)
    if required:
        cid = getattr(settings, required[0].lower(), "")
        csec = getattr(settings, required[1].lower(), "")
        if not (cid and csec):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"OAuth for {provider} requires COMPOSIO_API_KEY (or {required[0]} and {required[1]}) in backend/.env. "
                "Alternatively, you can connect immediately via the 'Direct API Key / Secret Token' tab."
            )
    try:
        return {"authorize_url": await service.start_oauth(actor, provider, department_id=department_id)}
    except ConnectorError as exc:
        raise _http_error(exc) from exc


@router.post("/direct", response_model=ConnectionView)
async def direct_connect(
    payload: DirectConnectRequest,
    tc: TenantCtx,
    service: Service,
) -> ConnectionView:
    """Connect service directly with API Key, Personal Access Token, or Bot Token."""
    if not payload.department_id or not payload.department_id.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "department_id is required for new connections",
        )
    actor = _require_dept_connection_permission(
        tc, payload.department_id, Permission.CONNECTIONS_MANAGE, require_active=True
    )
    try:
        return await service.direct_connect(
            actor,
            payload.provider,
            payload.external_account_id,
            payload.credentials,
            department_id=payload.department_id,
        )
    except ConnectorError as exc:
        raise _http_error(exc) from exc


@router.post("/composio/execute")
async def execute_composio_tool(
    payload: ComposioExecuteRequest,
    tc: TenantCtx,
) -> dict[str, Any]:
    """Execute an allowlisted Composio tool via ActionDispatcher with approval, idempotency, and audit controls."""
    import uuid

    from app.services import composio_service
    from app.workflows.actions import ActionDispatcher, ActionError

    _require_dept_connection_permission(
        tc, payload.department_id, Permission.CONNECTIONS_MANAGE, require_active=True
    )
    if not composio_service.is_composio_configured():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "COMPOSIO_API_KEY is not configured in backend/.env",
        )
    dept_uuid: uuid.UUID | None = None
    if payload.department_id:
        try:
            dept_uuid = uuid.UUID(str(payload.department_id))
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "department_id must be a valid UUID"
            ) from exc
    run_uuid: uuid.UUID | None = None
    if payload.run_id:
        try:
            run_uuid = uuid.UUID(str(payload.run_id))
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "run_id must be a valid UUID"
            ) from exc

    try:
        return ActionDispatcher.dispatch_composio(
            tc.db,
            tenant_id=tc.tenant_id,
            user_id=tc.user.id,
            department_id=dept_uuid,
            tool_slug=payload.tool_slug,
            arguments=payload.arguments,
            run_id=run_uuid,
            idempotency_key=payload.idempotency_key,
        )
    except ActionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except Exception as exc:
        logger.exception("Composio tool execution failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/oauth/callback")
async def finish_oauth(
    request: Request,
    service: Service,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
    composio: Annotated[str | None, Query()] = None,
    composio_provider: Annotated[str | None, Query()] = None,
    connectedAccountId: Annotated[str | None, Query()] = None,
):
    """Provider redirect target. Supports both Composio session callback and direct OAuth callback."""
    import urllib.parse

    from app.services import composio_service

    settings = get_settings()
    accept = request.headers.get("accept", "")
    wants_html = "text/html" in accept or "text/*" in accept or "*/*" in accept

    if error:
        err_msg = error_description or error
        logger.warning("Provider OAuth redirect returned error: %s (%s)", error, error_description)
        if wants_html:
            return RedirectResponse(
                url=f"{settings.frontend_url.rstrip('/')}/connectors?error={urllib.parse.quote_plus(err_msg)}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err_msg)

    is_composio_cb = bool(composio or composio_provider or (connectedAccountId and not code))
    if is_composio_cb:
        if not composio_service.is_composio_configured():
            err_msg = "Composio is not configured."
            if wants_html:
                return RedirectResponse(
                    url=f"{settings.frontend_url.rstrip('/')}/connectors?error={urllib.parse.quote_plus(err_msg)}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            raise HTTPException(status.HTTP_400_BAD_REQUEST, err_msg)
        if not state:
            err_msg = "Missing signed OAuth state for Composio callback."
            if wants_html:
                return RedirectResponse(
                    url=f"{settings.frontend_url.rstrip('/')}/connectors?error={urllib.parse.quote_plus(err_msg)}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            raise HTTPException(status.HTTP_400_BAD_REQUEST, err_msg)
        try:
            verified_tenant_id, verified_user_id, verified_provider, verified_dept_id = (
                await service.verify_composio_state(state)
            )
        except InvalidOAuthState as exc:
            err_msg = str(exc) or "Invalid or replayed OAuth state."
            if wants_html:
                return RedirectResponse(
                    url=f"{settings.frontend_url.rstrip('/')}/connectors?error={urllib.parse.quote_plus(err_msg)}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            raise HTTPException(status.HTTP_400_BAD_REQUEST, err_msg) from exc
        try:
            scoped_uid = _composio_scope_user_id(verified_tenant_id, verified_dept_id)
            tk_state = composio_service.get_toolkit_connection_state(scoped_uid, verified_provider)
            acct_id = (
                tk_state.get("connected_account_id")
                or connectedAccountId
                or f"composio:{verified_provider}:{(verified_dept_id or verified_tenant_id)[:8]}"
            )
            comp_actor = Actor(
                tenant_id=verified_tenant_id,
                user_id=verified_user_id,
                permissions=frozenset({CONNECTIONS_READ, CONNECTIONS_MANAGE}),
            )
            view = await service.direct_connect(
                comp_actor,
                verified_provider,
                str(acct_id),
                {"access_token": f"composio_managed:{acct_id}", "composio_managed": True},
                department_id=verified_dept_id,
            )
            if wants_html:
                return RedirectResponse(
                    url=f"{settings.frontend_url.rstrip('/')}/connectors?connected={view.id}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            return view
        except Exception as exc:
            logger.exception("Composio OAuth completion failed: %s", exc)
            err_msg = str(exc) or "Composio connection verification failed."
            if wants_html:
                return RedirectResponse(
                    url=f"{settings.frontend_url.rstrip('/')}/connectors?error={urllib.parse.quote_plus(err_msg)}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, err_msg) from exc

    if not code or not state:
        err_msg = "Missing authorization code or state from provider callback."
        if wants_html:
            return RedirectResponse(
                url=f"{settings.frontend_url.rstrip('/')}/connectors?error={urllib.parse.quote_plus(err_msg)}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err_msg)

    try:
        view = await service.finish_oauth(code, state)
        if wants_html:
            return RedirectResponse(
                url=f"{settings.frontend_url.rstrip('/')}/connectors?connected={view.id}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return view
    except Exception as exc:
        logger.exception("OAuth completion failed: %s", exc)
        err_msg = str(exc) or "OAuth verification failed."
        if wants_html:
            return RedirectResponse(
                url=f"{settings.frontend_url.rstrip('/')}/connectors?error={urllib.parse.quote_plus(err_msg)}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        err_to_raise = _http_error(exc) if isinstance(exc, ConnectorError) else HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
        raise err_to_raise from exc

@router.get("/{connection_id}", response_model=ConnectionView)
async def get_connection(connection_id: str, tc: TenantCtx, service: Service) -> ConnectionView:
    dept_id = _load_connection_dept_id(tc, connection_id)
    actor = _require_dept_connection_permission(tc, dept_id, Permission.CONNECTIONS_READ)
    try:
        return await service.health(actor, connection_id)
    except ConnectorError as exc:
        raise _http_error(exc) from exc

@router.post("/{connection_id}/poll")
async def poll_connection(connection_id: str, tc: TenantCtx, service: Service) -> dict:
    from app.services import composio_service
    from app.services import department as department_service

    dept_id = _load_connection_dept_id(tc, connection_id)
    actor = _require_dept_connection_permission(tc, dept_id, Permission.CONNECTIONS_MANAGE)

    if composio_service.is_composio_configured():
        try:
            import uuid
            from datetime import UTC, datetime

            from sqlalchemy import select

            from app.db.enums import EventStatus
            from app.db.models.connection import Connection
            from app.db.models.event import Event
            from app.db.rls import set_rls_context
            from app.db.session import get_session_factory
            from app.workflows.dept_executor import process_pending_department_events

            tid = uuid.UUID(str(actor.tenant_id))
            cid = uuid.UUID(str(connection_id))
            factory = get_session_factory()
            with factory() as db, db.begin():
                set_rls_context(db, tenant_id=tid)
                conn = db.scalar(
                    select(Connection).where(Connection.id == cid, Connection.tenant_id == tid)
                )
                if conn and conn.provider in ("gmail", "google_drive", "google_calendar"):
                    scoped_uid = _composio_scope_user_id(
                        actor.tenant_id, str(conn.department_id) if conn.department_id else None
                    )
                    raw_events = composio_service.fetch_provider_events(scoped_uid, conn.provider)
                    inserted = 0
                    for ev in raw_events:
                        exists = db.scalar(
                            select(Event.id).where(
                                Event.tenant_id == tid,
                                Event.connection_id == cid,
                                Event.dedup_key == ev["external_id"],
                            )
                        )
                        if not exists:
                            db.add(
                                Event(
                                    tenant_id=tid,
                                    connection_id=cid,
                                    department_id=conn.department_id,
                                    dedup_key=ev["external_id"],
                                    event_type=ev["event_type"],
                                    payload=ev["payload"],
                                    status=EventStatus.RECEIVED,
                                )
                            )
                            inserted += 1
                    conn.last_health_check_at = datetime.now(UTC)
                    db.flush()
                    dept_runs, matched_ids = process_pending_department_events(
                        db=db, tenant_id=tid, connection_id=cid
                    )
                    for mid in matched_ids:
                        ev_row = db.get(Event, mid)
                        if ev_row:
                            ev_row.status = EventStatus.PROCESSED
                            ev_row.processed_at = datetime.now(UTC)
                    db.flush()
                    return {
                        "status": "CONNECTED",
                        "fetched": len(raw_events),
                        "inserted": inserted,
                        "triggered_runs": len(dept_runs),
                    }
        except Exception as exc:
            logger.warning("Composio poll fallback for %s: %s", connection_id, exc)

    try:
        result = await service.poll(actor, connection_id)
    except ConnectorError as exc:
        raise _http_error(exc) from exc

    if result.status in (
        ConnectorConnectionStatus.NEEDS_REAUTH,
        ConnectorConnectionStatus.ERROR,
        ConnectorConnectionStatus.REVOKED,
    ):
        import uuid

        department_service.pause_workflows_for_connection(
            tc.db,
            tenant_id=tc.tenant_id,
            connection_id=uuid.UUID(str(connection_id)),
            reason=f"connection {result.status.value.lower()}",
            actor_id=tc.user.id,
        )

    triggered_runs = 0
    if result.inserted > 0:
        try:
            import uuid

            from app.db.rls import set_rls_context
            from app.db.session import get_session_factory
            from app.workflows.dept_executor import process_pending_department_events

            tid = uuid.UUID(str(actor.tenant_id))
            cid = uuid.UUID(str(connection_id))
            factory = get_session_factory()
            with factory() as db, db.begin():
                set_rls_context(db, tenant_id=tid)
                dept_runs, _ = process_pending_department_events(
                    db=db, tenant_id=tid, connection_id=cid
                )
                triggered_runs = len(dept_runs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to auto-trigger workflows after poll: %s", exc)

    return {
        "status": result.status.value,
        "fetched": result.fetched,
        "inserted": result.inserted,
        "triggered_runs": triggered_runs,
    }

@router.delete("/{connection_id}")
async def revoke_connection(
    connection_id: str,
    tc: TenantCtx,
    service: Service,
    permanent: bool = Query(False),
) -> dict[str, Any]:
    import uuid

    from app.services import department as department_service

    dept_id = _load_connection_dept_id(tc, connection_id)
    actor = _require_dept_connection_permission(tc, dept_id, Permission.CONNECTIONS_MANAGE)
    try:
        cid = uuid.UUID(str(connection_id))
        from app.db.rls import set_rls_context
        from app.db.session import get_session_factory

        with get_session_factory()() as pdb, pdb.begin():
            set_rls_context(pdb, user_id=tc.user.id, tenant_id=tc.tenant_id)
            paused = department_service.pause_workflows_for_connection(
                pdb,
                tenant_id=tc.tenant_id,
                connection_id=cid,
                reason="connection deleted" if permanent else "connection disconnected",
                actor_id=tc.user.id,
            )
        if permanent:
            await service.delete(actor, connection_id)
        else:
            await service.revoke(actor, connection_id)
    except ConnectorError as exc:
        raise _http_error(exc) from exc
    return {
        "status": "deleted" if permanent else "revoked",
        "paused_workflows": paused,
    }
