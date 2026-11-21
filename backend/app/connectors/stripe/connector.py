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

API = "https://api.stripe.com/v1"

@register("stripe")
class StripeConnector(OAuth2Connector):
    auth_endpoint = "https://connect.stripe.com/oauth/authorize"
    token_endpoint = "https://connect.stripe.com/oauth/token"
    revoke_endpoint = "https://connect.stripe.com/oauth/deauthorize"
    scopes = ["read_write"]
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
            raise TokenExpired("stripe api key rejected or expired")
        if resp.status == 429:
            raise RateLimited(5.0)
        if resp.status == 403:
            raise ReauthRequired("stripe access permissions revoked")
        raise ConnectorError(f"stripe api error: {resp.status}")

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        resp = await self.http.request(
            "POST",
            self.token_endpoint,
            form={
                "grant_type": "authorization_code",
                "client_secret": self._secret,
                "code": code,
            },
        )
        self._raise_for(resp)
        data = resp.body if isinstance(resp.body, dict) else {}
        access_token = data.get("access_token") or data.get("stripe_user_id")
        return TokenSet(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope"),
        )

    async def fetch_account_id(self, tokens: TokenSet) -> str:
        resp = await self.http.request(
            "GET",
            f"{API}/account",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        self._raise_for(resp)
        data = resp.body if isinstance(resp.body, dict) else {}
        acct_id = data.get("id") or "Stripe Account"
        name = (data.get("business_profile") or {}).get("name")
        return f"{acct_id} ({name})" if name else acct_id

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            return tokens
        resp = await self.http.request(
            "POST",
            self.token_endpoint,
            form={
                "grant_type": "refresh_token",
                "client_secret": self._secret,
                "refresh_token": tokens.refresh_token,
            },
        )
        self._raise_for(resp)
        data = resp.body if isinstance(resp.body, dict) else {}
        return TokenSet(
            access_token=data.get("access_token") or tokens.access_token,
            refresh_token=data.get("refresh_token") or tokens.refresh_token,
        )

    async def fetch_events(self, tokens: TokenSet, since_cursor: str | None) -> EventBatch:
        params = {"limit": "10"}
        if since_cursor:
            params["starting_after"] = since_cursor

        resp = await self.http.request(
            "GET",
            f"{API}/events",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            params=params,
        )
        self._raise_for(resp)
        data = (resp.body if isinstance(resp.body, dict) else {}).get("data", [])
        events = [
            Event(
                external_id=e["id"],
                event_type=f"stripe.{e.get('type')}",
                occurred_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e.get("created", self._now()))),
                payload=e.get("data", {}).get("object", {}),
            )
            for e in data
        ]
        next_cur = data[-1]["id"] if data else since_cursor
        return EventBatch(events=events, next_cursor=next_cur)

    async def revoke(self, tokens: TokenSet) -> None:
        try:
            await self.http.request(
                "POST",
                self.revoke_endpoint,
                form={"client_secret": self._secret, "stripe_user_id": tokens.access_token},
            )
        except Exception:
            pass

    async def health(self, tokens: TokenSet) -> Health:
        try:
            resp = await self.http.request(
                "GET",
                f"{API}/balance",
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
