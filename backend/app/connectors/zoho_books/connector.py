"""Zoho Books Free-Plan OAuth2 connector (REST API v3, 100 req/min, 1,000 req/day, 5 concurrent)."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from ..base import (
    ConnectionStatus,
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

ZOHO_AUTH_URL = "https://accounts.zoho.com/oauth/v2/auth"
ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_REVOKE_URL = "https://accounts.zoho.com/oauth/v2/token/revoke"
ZOHO_BOOKS_API = "https://www.zohoapis.com/books/v3"


@register("zoho_books")
class ZohoBooksConnector(OAuth2Connector):
    """Zoho Books OAuth2 connector for supplier bills & AP (100/min, 1000/day, 5 concurrent)."""

    auth_endpoint = ZOHO_AUTH_URL
    token_endpoint = ZOHO_TOKEN_URL
    revoke_endpoint = ZOHO_REVOKE_URL
    scopes = [
        "ZohoBooks.bills.READ",
        "ZohoBooks.bills.CREATE",
        "ZohoBooks.bills.UPDATE",
        "ZohoBooks.contacts.READ",
    ]
    extra_auth_params = {"access_type": "offline", "prompt": "consent"}
    max_requests_per_minute = 100

    def __init__(
        self,
        http: HttpClient,
        client_id: str,
        client_secret: str,
        *,
        api_base: str = ZOHO_BOOKS_API,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.http = http
        self.client_id = client_id
        self._secret = client_secret
        self._api_base = api_base.rstrip("/")
        self._now = now
        self._semaphore = asyncio.Semaphore(5)

    @staticmethod
    def _raise_for(resp: HttpResponse) -> None:
        s = resp.status
        if s < 400:
            return
        if s == 401:
            raise TokenExpired("Zoho Books access token rejected")
        if s == 429:
            ra = resp.headers.get("Retry-After")
            raise RateLimited(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 5.0)
        if s == 403:
            raise ReauthRequired("Zoho Books insufficient permission or revoked")
        msg = ""
        if isinstance(resp.body, dict):
            msg = str(resp.body.get("message") or "")
        raise ConnectorError(f"Zoho Books API error {s}: {msg}" if msg else f"Zoho Books API error {s}")

    async def _request(self, method: str, path: str, tokens: TokenSet, **kwargs) -> HttpResponse:
        async with self._semaphore:
            headers = {"Authorization": f"Zoho-oauthtoken {tokens.access_token}"}
            resp = await self.http.request(method, f"{self._api_base}/{path.lstrip('/')}", headers=headers, **kwargs)
            self._raise_for(resp)
            return resp

    def _token_set(self, body: dict[str, Any], old_refresh: str | None = None) -> TokenSet:
        expires_in = body.get("expires_in")
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or old_refresh,
            expires_at=self._now() + float(expires_in) if expires_in else None,
            scope=body.get("scope"),
        )

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
            },
        )
        self._raise_for(resp)
        return self._token_set(resp.body)

    async def refresh(self, refresh_token: str) -> TokenSet:
        resp = await self.http.request(
            "POST",
            self.token_endpoint,
            form={
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self._secret,
                "grant_type": "refresh_token",
            },
        )
        self._raise_for(resp)
        return self._token_set(resp.body, old_refresh=refresh_token)

    async def revoke(self, token: str) -> None:
        await self.http.request("POST", self.revoke_endpoint, form={"token": token})

    async def health(self, tokens: TokenSet) -> Health:
        try:
            resp = await self._request("GET", "organizations", tokens)
            orgs = (resp.body or {}).get("organizations") or []
            org_name = orgs[0].get("name") if orgs else None
            return Health(status=ConnectionStatus.HEALTHY, account_label=org_name)
        except TokenExpired as exc:
            return Health(status=ConnectionStatus.EXPIRED, detail=str(exc))
        except ReauthRequired as exc:
            return Health(status=ConnectionStatus.REVOKED, detail=str(exc))
        except ConnectorError as exc:
            return Health(status=ConnectionStatus.ERROR, detail=str(exc))

    async def poll(self, tokens: TokenSet, cursor: str | None) -> EventBatch:
        params: dict[str, Any] = {"per_page": 25}
        if cursor:
            params["last_modified_time"] = cursor
        resp = await self._request("GET", "bills", tokens, params=params)
        bills = (resp.body or {}).get("bills") or []
        events = [
            {
                "event_type": "zoho_books.bill_updated",
                "external_id": str(b.get("bill_id") or ""),
                "payload": b,
            }
            for b in bills
            if b.get("bill_id")
        ]
        return EventBatch(events=events, next_cursor=cursor)

    async def fetch_account_id(self, tokens: TokenSet) -> str:
        resp = await self._request("GET", "organizations", tokens)
        orgs = (resp.body or {}).get("organizations") or []
        if orgs and orgs[0].get("organization_id"):
            return str(orgs[0]["organization_id"])
        return "zoho_books_default"

    async def fetch_events(self, tokens: TokenSet, cursor: str | None) -> EventBatch:
        return await self.poll(tokens, cursor)

    async def create_bill(
        self,
        tokens: TokenSet,
        *,
        organization_id: str | None = None,
        bill_data: dict[str, Any],
    ) -> dict[str, Any]:
        params = {"organization_id": organization_id} if organization_id else None
        resp = await self._request("POST", "bills", tokens, params=params, json=bill_data)
        return dict(resp.body or {})
