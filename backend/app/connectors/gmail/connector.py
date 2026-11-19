from __future__ import annotations

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

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

API = "https://gmail.googleapis.com/gmail/v1/users/me"
KEEP_HEADERS = {"from", "to", "cc", "subject", "date"}
_CV_EXTENSIONS = (".pdf", ".docx", ".txt")
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class GmailAttachment:
    filename: str
    mime_type: str
    data: bytes

@register("gmail")
class GmailConnector(OAuth2Connector):
    auth_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    revoke_endpoint = "https://oauth2.googleapis.com/revoke"
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    extra_auth_params = {"access_type": "offline", "prompt": "consent"}
    max_requests_per_minute = 250

    def __init__(
        self, http: HttpClient, client_id: str, client_secret: str, *,
        backfill_days: int = 30, max_backfill: int = 200,
        now: Callable[[], float] = time.time,
    ):
        self.http = http
        self.client_id = client_id
        self._secret = client_secret
        self.backfill_days = backfill_days
        self.max_backfill = max_backfill
        self._now = now

    
    @staticmethod
    def _raise_for(resp: HttpResponse) -> None:
        s = resp.status
        if s < 400:
            return
        if s == 401:
            raise TokenExpired("access token rejected")
        reason = ""
        try:
            reason = resp.body["error"]["errors"][0]["reason"]
        except (KeyError, IndexError, TypeError):
            pass
        if s == 429 or (s == 403 and reason in ("rateLimitExceeded", "userRateLimitExceeded")):
            ra = resp.headers.get("Retry-After")
            raise RateLimited(float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
        if s == 403:
            raise ReauthRequired("insufficient permission or access revoked")
        raise ConnectorError(f"gmail api error {s}")

    async def _get(self, tokens: TokenSet, path: str, params: dict | None = None) -> HttpResponse:
        resp = await self.http.request(
            "GET", f"{API}/{path}",
            headers={"Authorization": f"Bearer {tokens.access_token}"}, params=params,
        )
        return resp

    async def _get_ok(self, tokens, path, params=None) -> HttpResponse:
        resp = await self._get(tokens, path, params)
        self._raise_for(resp)
        return resp

    def _token_set(self, body: dict, old_refresh: str | None = None) -> TokenSet:
        expires_in = body.get("expires_in")
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or old_refresh,
            expires_at=self._now() + float(expires_in) if expires_in else None,
            scope=body.get("scope"),
        )

    
    async def exchange_code(self, code, code_verifier, redirect_uri) -> TokenSet:
        resp = await self.http.request("POST", self.token_endpoint, form={
            "code": code, "client_id": self.client_id, "client_secret": self._secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        })
        if resp.status != 200 or "access_token" not in resp.body:
            raise ConnectorError("token exchange failed")
        tokens = self._token_set(resp.body)
        if not tokens.refresh_token:
            raise ConnectorError("no refresh token returned; reconnect and grant consent again")
        return tokens

    async def fetch_account_id(self, tokens) -> str:
        resp = await self._get_ok(tokens, "profile")
        return resp.body["emailAddress"].lower()

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise ReauthRequired("no refresh token stored")
        resp = await self.http.request("POST", self.token_endpoint, form={
            "client_id": self.client_id, "client_secret": self._secret,
            "refresh_token": tokens.refresh_token, "grant_type": "refresh_token",
        })
        if resp.status == 400 and resp.body.get("error") == "invalid_grant":
            raise ReauthRequired("invalid_grant")
        if resp.status == 429:
            raise RateLimited(None)
        if resp.status != 200 or "access_token" not in resp.body:
            raise ConnectorError(f"token refresh failed ({resp.status})")
        return self._token_set(resp.body, old_refresh=tokens.refresh_token)

    async def revoke(self, tokens: TokenSet) -> None:
        token = tokens.refresh_token or tokens.access_token
        try:                                               
            await self.http.request("POST", self.revoke_endpoint, form={"token": token})
        except Exception:  # noqa: BLE001
            pass

    
    async def fetch_events(self, tokens: TokenSet, since_cursor: str | None) -> EventBatch:
        if since_cursor is None:
            return await self._initial_sync(tokens)
        return await self._incremental_sync(tokens, since_cursor)

    async def _initial_sync(self, tokens) -> EventBatch:
        profile = (await self._get_ok(tokens, "profile")).body
        history_id = str(profile["historyId"])                                                
        ids: list[str] = []
        page = None
        while len(ids) < self.max_backfill:
            params = {"q": f"newer_than:{self.backfill_days}d", "maxResults": min(100, self.max_backfill - len(ids))}
            if page:
                params["pageToken"] = page
            body = (await self._get_ok(tokens, "messages", params)).body
            ids += [m["id"] for m in body.get("messages", [])]
            page = body.get("nextPageToken")
            if not page:
                break
        return EventBatch(await self._load_messages(tokens, ids), next_cursor=history_id)

    async def _incremental_sync(self, tokens, cursor) -> EventBatch:
        ids: list[str] = []
        seen: set[str] = set()
        next_cursor = cursor
        page = None
        while True:
            params = {"startHistoryId": cursor, "historyTypes": "messageAdded", "maxResults": 100}
            if page:
                params["pageToken"] = page
            resp = await self._get(tokens, "history", params)
            if resp.status == 404:                     
                return EventBatch([], None, reset_required=True)
            self._raise_for(resp)
            body = resp.body
            for h in body.get("history", []):
                for added in h.get("messagesAdded", []):
                    mid = added["message"]["id"]
                    if mid not in seen:
                        seen.add(mid)
                        ids.append(mid)
            next_cursor = str(body.get("historyId", next_cursor))
            page = body.get("nextPageToken")
            if not page:
                break
        return EventBatch(await self._load_messages(tokens, ids), next_cursor=next_cursor)

    async def _load_messages(self, tokens, ids: list[str]) -> list[Event]:
        events = []
        for mid in ids:
            resp = await self._get(tokens, f"messages/{mid}", {
                "format": "metadata", "metadataHeaders": sorted(KEEP_HEADERS),
            })
            if resp.status == 404:                                
                continue
            self._raise_for(resp)
            events.append(self._to_event(resp.body))
        return events

    @staticmethod
    def _to_event(msg: dict) -> Event:
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
            if h.get("name", "").lower() in KEEP_HEADERS
        }
        ts = msg.get("internalDate")
        occurred = (
            datetime.fromtimestamp(int(ts) / 1000, tz=UTC).isoformat() if ts else None
        )
        return Event(
            external_id=msg["id"],
            event_type="email.received",
            occurred_at=occurred,
            payload={
                "thread_id": msg.get("threadId"),
                "labels": msg.get("labelIds", []),
                "snippet": msg.get("snippet", ""),
                "headers": headers,
            },
        )

    async def health(self, tokens) -> Health:
        try:
            await self._get_ok(tokens, "profile")
            return Health(ConnectionStatus.CONNECTED)
        except RateLimited as e:
            return Health(ConnectionStatus.RATE_LIMITED, "rate limited", e.retry_after)
        except (ReauthRequired, TokenExpired):
            return Health(ConnectionStatus.NEEDS_REAUTH, "re-authorization required")
        except ConnectorError as e:
            return Health(ConnectionStatus.ERROR, str(e))

    async def fetch_message_attachments(
        self,
        tokens: TokenSet,
        message_id: str,
    ) -> list[GmailAttachment]:
        """Download supported CV attachments (.pdf, .docx, .txt) for a Gmail message."""
        msg_resp = await self._get_ok(tokens, f"messages/{message_id}", {"format": "full"})
        payload = msg_resp.body.get("payload") if isinstance(msg_resp.body, dict) else None
        if not isinstance(payload, dict):
            return []

        attachments: list[GmailAttachment] = []
        stack: list[dict] = [payload]
        while stack:
            part = stack.pop()
            subparts = part.get("parts")
            if isinstance(subparts, list):
                stack.extend(p for p in subparts if isinstance(p, dict))

            filename = str(part.get("filename") or "").strip()
            if not filename or not filename.lower().endswith(_CV_EXTENSIONS):
                continue

            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            size_hint = int(body.get("size") or 0)
            if size_hint > _MAX_ATTACHMENT_BYTES:
                continue

            raw_b64 = body.get("data")
            if not raw_b64 and body.get("attachmentId"):
                att_resp = await self._get_ok(
                    tokens,
                    f"messages/{message_id}/attachments/{body['attachmentId']}",
                )
                att_body = att_resp.body if isinstance(att_resp.body, dict) else {}
                raw_b64 = att_body.get("data")

            if not isinstance(raw_b64, str) or not raw_b64:
                continue

            padded = raw_b64 + "=" * (-len(raw_b64) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            if 0 < len(decoded) <= _MAX_ATTACHMENT_BYTES:
                attachments.append(
                    GmailAttachment(
                        filename=filename,
                        mime_type=str(part.get("mimeType") or "application/octet-stream"),
                        data=decoded,
                    )
                )

        return attachments
