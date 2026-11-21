from __future__ import annotations

import time
from collections.abc import Callable

from ..base import (
    ConnectionStatus,
    Event,
    EventBatch,
    Health,
    OAuth2Connector,
    TokenSet,
)
from ..errors import (
    ConnectorError,
    RateLimited,
    ReauthRequired,
    TokenExpired,
)
from ..http import HttpClient, HttpResponse
from ..registry import register

API = "https://www.googleapis.com/calendar/v3"

@register("google_calendar")
class GoogleCalendarConnector(OAuth2Connector):
    auth_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    revoke_endpoint = "https://oauth2.googleapis.com/revoke"
    scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
    extra_auth_params = {"access_type": "offline", "prompt": "consent"}
    max_requests_per_minute = 240

    def __init__(
        self,
        http: HttpClient,
        client_id: str,
        client_secret: str,
        *,
        now: Callable[[], float] = time.time,
    ):
        self.http = http
        self.client_id = client_id
        self._secret = client_secret
        self._now = now

    @staticmethod
    def _raise_for(resp: HttpResponse) -> None:
        if resp.status < 400:
            return
        if resp.status == 401:
            raise TokenExpired("access token rejected")
        if resp.status == 429:
            ra = resp.headers.get("Retry-After")
            raise RateLimited(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 5.0)
        if resp.status == 403:
            detail = ""
            if isinstance(resp.body, dict):
                error_obj = resp.body.get("error")
                if isinstance(error_obj, dict):
                    detail = error_obj.get("message") or ""
                elif isinstance(error_obj, str):
                    detail = error_obj
            msg = f"Google Calendar API 403: {detail}" if detail else "insufficient permission or calendar access revoked"
            raise ReauthRequired(msg)
        raise ConnectorError(f"google calendar api error: {resp.status}")

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        resp = await self.http.request(
            "POST",
            self.token_endpoint,
            form={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self._secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
        )
        self._raise_for(resp)
        data = resp.body if isinstance(resp.body, dict) else {}
        expires_in = data.get("expires_in")
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=self._now() + float(expires_in) if expires_in else None,
            scope=data.get("scope"),
        )

    async def fetch_account_id(self, tokens: TokenSet) -> str:
        resp = await self.http.request(
            "GET",
            f"{API}/users/me/calendarList/primary",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        self._raise_for(resp)
        data = resp.body if isinstance(resp.body, dict) else {}
        return data.get("id") or data.get("summary") or "Primary Calendar"

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            return tokens
        resp = await self.http.request(
            "POST",
            self.token_endpoint,
            form={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self._secret,
                "refresh_token": tokens.refresh_token,
            },
        )
        self._raise_for(resp)
        data = resp.body if isinstance(resp.body, dict) else {}
        expires_in = data.get("expires_in")
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=tokens.refresh_token,
            expires_at=self._now() + float(expires_in) if expires_in else None,
            scope=data.get("scope") or tokens.scope,
        )

    async def fetch_events(self, tokens: TokenSet, since_cursor: str | None) -> EventBatch:
        params = {
            "maxResults": "10",
            "singleEvents": "true",
            "orderBy": "updated",
        }
        if since_cursor:
            params["updatedMin"] = since_cursor

        resp = await self.http.request(
            "GET",
            f"{API}/calendars/primary/events",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            params=params,
        )
        self._raise_for(resp)
        items = (resp.body if isinstance(resp.body, dict) else {}).get("items", [])
        events = [
            Event(
                external_id=item["id"],
                event_type="calendar.event",
                occurred_at=item.get("updated") or item.get("start", {}).get("dateTime"),
                payload={
                    "summary": item.get("summary"),
                    "status": item.get("status"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                },
            )
            for item in items
        ]
        next_cur = items[-1].get("updated") if items else since_cursor
        return EventBatch(events=events, next_cursor=next_cur)

    async def revoke(self, tokens: TokenSet) -> None:
        try:
            await self.http.request(
                "POST",
                self.revoke_endpoint,
                form={"token": tokens.access_token},
            )
        except Exception:
            pass

    async def health(self, tokens: TokenSet) -> Health:
        try:
            resp = await self.http.request(
                "GET",
                f"{API}/users/me/calendarList/primary",
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
            self._raise_for(resp)
            return Health(ConnectionStatus.CONNECTED)
        except RateLimited as exc:
            return Health(ConnectionStatus.RATE_LIMITED, retry_after=exc.retry_after)
        except ReauthRequired as exc:
            return Health(ConnectionStatus.NEEDS_REAUTH, last_error=str(exc))
        except Exception as exc:
            return Health(ConnectionStatus.ERROR, last_error=str(exc))
