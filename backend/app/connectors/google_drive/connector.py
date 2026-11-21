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

API = "https://www.googleapis.com/drive/v3"

@register("google_drive")
class GoogleDriveConnector(OAuth2Connector):
    auth_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    revoke_endpoint = "https://oauth2.googleapis.com/revoke"
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
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
            msg = f"Google Drive API 403: {detail}" if detail else "insufficient permission or drive access revoked"
            raise ReauthRequired(msg)
        raise ConnectorError(f"google drive api error: {resp.status}")

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
            f"{API}/about",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            params={"fields": "user"},
        )
        self._raise_for(resp)
        user = (resp.body if isinstance(resp.body, dict) else {}).get("user", {})
        return user.get("emailAddress") or user.get("displayName") or "Google Drive Account"

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
        resp = await self.http.request(
            "GET",
            f"{API}/files",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            params={
                "pageSize": "10",
                "orderBy": "modifiedTime desc",
                "fields": "files(id, name, mimeType, modifiedTime, size)",
            },
        )
        self._raise_for(resp)
        files = (resp.body if isinstance(resp.body, dict) else {}).get("files", [])
        events = [
            Event(
                external_id=f["id"],
                event_type="drive.file_updated",
                occurred_at=f.get("modifiedTime"),
                payload={
                    "name": f.get("name"),
                    "mimeType": f.get("mimeType"),
                    "size": f.get("size"),
                },
            )
            for f in files
            if not since_cursor or (f.get("modifiedTime") and f.get("modifiedTime") > since_cursor)
        ]
        next_cur = files[0].get("modifiedTime") if files else since_cursor
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
                f"{API}/about",
                headers={"Authorization": f"Bearer {tokens.access_token}"},
                params={"fields": "user"},
            )
            self._raise_for(resp)
            return Health(ConnectionStatus.CONNECTED)
        except RateLimited as exc:
            return Health(ConnectionStatus.RATE_LIMITED, retry_after=exc.retry_after)
        except ReauthRequired as exc:
            return Health(ConnectionStatus.NEEDS_REAUTH, last_error=str(exc))
        except Exception as exc:
            return Health(ConnectionStatus.ERROR, last_error=str(exc))
