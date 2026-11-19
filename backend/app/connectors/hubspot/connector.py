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

API = "https://api.hubapi.com"

@register("hubspot")
class HubSpotConnector(OAuth2Connector):
    auth_endpoint = "https://app.hubspot.com/oauth/authorize"
    token_endpoint = "https://api.hubapi.com/oauth/v1/token"
    scopes = ["crm.objects.contacts.read", "crm.objects.deals.read"]
    max_requests_per_minute = 100

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
            raise TokenExpired("hubspot token invalid or expired")
        if resp.status == 429:
            ra = resp.headers.get("Retry-After")
            raise RateLimited(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 10.0)
        if resp.status == 403:
            raise ReauthRequired("hubspot permissions revoked or expired")
        raise ConnectorError(f"hubspot api error: {resp.status}")

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        resp = await self.http.request(
            "POST",
            self.token_endpoint,
            form={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self._secret,
                "redirect_uri": redirect_uri,
                "code": code,
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
            f"{API}/oauth/v1/access-tokens/{tokens.access_token}",
        )
        self._raise_for(resp)
        data = resp.body if isinstance(resp.body, dict) else {}
        hub_id = data.get("hub_id") or data.get("portal_id")
        user = data.get("user")
        return f"HubSpot Portal {hub_id}" if hub_id else (user or "HubSpot CRM")

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
            refresh_token=data.get("refresh_token") or tokens.refresh_token,
            expires_at=self._now() + float(expires_in) if expires_in else None,
            scope=data.get("scope") or tokens.scope,
        )

    async def fetch_events(self, tokens: TokenSet, since_cursor: str | None) -> EventBatch:
        resp = await self.http.request(
            "GET",
            f"{API}/crm/v3/objects/contacts",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            params={"limit": "10", "archived": "false"},
        )
        self._raise_for(resp)
        results = (resp.body if isinstance(resp.body, dict) else {}).get("results", [])
        events = [
            Event(
                external_id=c["id"],
                event_type="hubspot.contact_updated",
                occurred_at=c.get("updatedAt"),
                payload=c.get("properties", {}),
            )
            for c in results
        ]
        next_cur = results[0].get("updatedAt") if results else since_cursor
        return EventBatch(events=events, next_cursor=next_cur)

    async def revoke(self, tokens: TokenSet) -> None:
        try:
            await self.http.request(
                "POST",
                f"{API}/oauth/v1/revoke-tokens",
                form={"token": tokens.access_token},
            )
        except Exception:
            pass

    async def health(self, tokens: TokenSet) -> Health:
        try:
            resp = await self.http.request(
                "GET",
                f"{API}/oauth/v1/access-tokens/{tokens.access_token}",
            )
            self._raise_for(resp)
            return Health(ConnectionStatus.CONNECTED)
        except RateLimited as exc:
            return Health(ConnectionStatus.RATE_LIMITED, retry_after=exc.retry_after)
        except ReauthRequired as exc:
            return Health(ConnectionStatus.NEEDS_REAUTH, last_error=str(exc))
        except Exception as exc:
            return Health(ConnectionStatus.ERROR, last_error=str(exc))
