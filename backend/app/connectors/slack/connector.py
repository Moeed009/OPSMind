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
)
from ..http import HttpClient, HttpResponse
from ..registry import register

API = "https://slack.com/api"

@register("slack")
class SlackConnector(OAuth2Connector):
    auth_endpoint = "https://slack.com/oauth/v2/authorize"
    token_endpoint = "https://slack.com/api/oauth.v2.access"
    scopes = [
        "channels:read",
        "channels:history",
        "chat:write",
        "users:read",
    ]
    max_requests_per_minute = 180

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
        if resp.status == 429:
            ra = resp.headers.get("Retry-After")
            raise RateLimited(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 5.0)
        if resp.status >= 500:
            raise ConnectorError(f"slack server error: {resp.status}")
        body = resp.body if isinstance(resp.body, dict) else {}
        if not body.get("ok", True):
            err = body.get("error", "unknown_error")
            if err in ("invalid_auth", "token_revoked", "account_inactive", "token_expired"):
                raise ReauthRequired(f"slack auth failed: {err}")
            if err in ("ratelimited",):
                raise RateLimited(5.0)
            raise ConnectorError(f"slack api error: {err}")

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        resp = await self.http.request(
            "POST",
            self.token_endpoint,
            form={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self._secret,
                "redirect_uri": redirect_uri,
            },
        )
        self._raise_for(resp)
        data = resp.body if isinstance(resp.body, dict) else {}
        access_token = data.get("access_token") or (data.get("authed_user") or {}).get("access_token")
        if not access_token:
            raise ConnectorError("no access token returned by slack")
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in")
        return TokenSet(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=self._now() + float(expires_in) if expires_in else None,
            scope=data.get("scope"),
        )

    async def fetch_account_id(self, tokens: TokenSet) -> str:
        resp = await self.http.request(
            "POST",
            f"{API}/auth.test",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        self._raise_for(resp)
        data = resp.body if isinstance(resp.body, dict) else {}
        team = data.get("team") or "Slack Team"
        user = data.get("user") or "Bot"
        return f"{team} ({user})"

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
            access_token=data.get("access_token") or tokens.access_token,
            refresh_token=data.get("refresh_token") or tokens.refresh_token,
            expires_at=self._now() + float(expires_in) if expires_in else None,
            scope=data.get("scope") or tokens.scope,
        )

    async def fetch_events(self, tokens: TokenSet, since_cursor: str | None) -> EventBatch:
        resp = await self.http.request(
            "GET",
            f"{API}/conversations.list",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            params={"types": "public_channel,private_channel", "limit": "10"},
        )
        try:
            self._raise_for(resp)
        except Exception:
            return EventBatch(events=[], next_cursor=since_cursor)

        channels = (resp.body if isinstance(resp.body, dict) else {}).get("channels", [])
        events: list[Event] = []
        for ch in channels[:3]:
            ch_id = ch.get("id")
            if not ch_id:
                continue
            hist_resp = await self.http.request(
                "GET",
                f"{API}/conversations.history",
                headers={"Authorization": f"Bearer {tokens.access_token}"},
                params={"channel": ch_id, "limit": "5"},
            )
            if hist_resp.status == 200:
                messages = (hist_resp.body if isinstance(hist_resp.body, dict) else {}).get("messages", [])
                for msg in messages:
                    ts = msg.get("ts")
                    if ts and (not since_cursor or ts > since_cursor):
                        events.append(
                            Event(
                                external_id=f"{ch_id}:{ts}",
                                event_type="slack.message",
                                occurred_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts))),
                                payload={"channel": ch_id, "text": msg.get("text", ""), "user": msg.get("user")},
                            )
                        )
        next_cur = max([e.external_id.split(":")[-1] for e in events] + [since_cursor or "0"])
        return EventBatch(events=events, next_cursor=next_cur)

    async def revoke(self, tokens: TokenSet) -> None:
        try:
            await self.http.request(
                "POST",
                f"{API}/auth.revoke",
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
        except Exception:
            pass

    async def health(self, tokens: TokenSet) -> Health:
        try:
            resp = await self.http.request(
                "POST",
                f"{API}/auth.test",
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
