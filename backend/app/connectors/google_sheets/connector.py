"""Shared Google Sheets OAuth2 connector and spreadsheet action methods."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

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

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


@register("google_sheets")
class GoogleSheetsConnector(OAuth2Connector):
    """Shared Google Sheets connector reusing OpsMind's OAuth2Connector framework."""

    auth_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    revoke_endpoint = "https://oauth2.googleapis.com/revoke"
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/userinfo.email",
    ]
    extra_auth_params = {"access_type": "offline", "prompt": "consent"}
    max_requests_per_minute = 240

    def __init__(
        self,
        http: HttpClient,
        client_id: str,
        client_secret: str,
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.http = http
        self.client_id = client_id
        self._secret = client_secret
        self._now = now

    @staticmethod
    def _raise_for(resp: HttpResponse) -> None:
        s = resp.status
        if s < 400:
            return
        if s == 401:
            raise TokenExpired("Google Sheets access token rejected")
        if s == 429:
            ra = resp.headers.get("Retry-After")
            raise RateLimited(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 5.0)
        detail = ""
        if isinstance(resp.body, dict):
            err = resp.body.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or "")
            elif isinstance(err, str):
                detail = err
        if s == 403:
            raise ReauthRequired(
                f"Google Sheets API 403: {detail}" if detail else "insufficient permission or access revoked"
            )
        raise ConnectorError(
            f"Google Sheets API error {s}: {detail}" if detail else f"Google Sheets API error {s}"
        )

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
                "code_verifier": code_verifier,
            },
        )
        if resp.status != 200 or not isinstance(resp.body, dict) or "access_token" not in resp.body:
            raise ConnectorError("Google Sheets token exchange failed")
        tokens = self._token_set(resp.body)
        if not tokens.refresh_token:
            raise ConnectorError("no refresh token returned; reconnect and grant consent again")
        return tokens

    async def fetch_account_id(self, tokens: TokenSet) -> str:
        resp = await self.http.request(
            "GET",
            USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        self._raise_for(resp)
        email = resp.body.get("email") if isinstance(resp.body, dict) else None
        if not email:
            raise ConnectorError("Google userinfo did not return an email address")
        return str(email).lower()

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise ReauthRequired("no refresh token stored")
        resp = await self.http.request(
            "POST",
            self.token_endpoint,
            form={
                "refresh_token": tokens.refresh_token,
                "client_id": self.client_id,
                "client_secret": self._secret,
                "grant_type": "refresh_token",
            },
        )
        if resp.status == 400 and isinstance(resp.body, dict) and resp.body.get("error") == "invalid_grant":
            raise ReauthRequired("Google Sheets refresh token revoked or expired")
        if resp.status != 200 or not isinstance(resp.body, dict) or "access_token" not in resp.body:
            raise ConnectorError(f"Google Sheets token refresh failed: {resp.status}")
        return self._token_set(resp.body, old_refresh=tokens.refresh_token)

    async def fetch_events(self, tokens: TokenSet, since_cursor: str | None) -> EventBatch:
        return EventBatch(events=[], next_cursor=since_cursor)

    async def revoke(self, tokens: TokenSet) -> None:
        token = tokens.refresh_token or tokens.access_token
        if not token:
            return
        await self.http.request("POST", self.revoke_endpoint, form={"token": token})

    async def health(self, tokens: TokenSet) -> Health:
        try:
            resp = await self.http.request(
                "GET",
                USERINFO_URL,
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
            self._raise_for(resp)
            return Health(status=ConnectionStatus.CONNECTED)
        except ReauthRequired as exc:
            return Health(status=ConnectionStatus.NEEDS_REAUTH, last_error=str(exc))
        except RateLimited as exc:
            return Health(
                status=ConnectionStatus.RATE_LIMITED,
                last_error="rate limited",
                retry_after=exc.retry_after,
            )
        except TokenExpired as exc:
            return Health(status=ConnectionStatus.NEEDS_REAUTH, last_error=str(exc))
        except ConnectorError as exc:
            return Health(status=ConnectionStatus.ERROR, last_error=str(exc))

    # ------------------------------------------------------------------
    # Google Sheets v4 API operations
    # ------------------------------------------------------------------

    async def create_spreadsheet(
        self,
        tokens: TokenSet,
        *,
        title: str,
        sheet_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"properties": {"title": title}}
        if sheet_titles:
            payload["sheets"] = [{"properties": {"title": name}} for name in sheet_titles]
        resp = await self.http.request(
            "POST",
            SHEETS_API,
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            json=payload,
        )
        self._raise_for(resp)
        body = resp.body if isinstance(resp.body, dict) else {}
        sheets = [
            {
                "sheet_id": s.get("properties", {}).get("sheetId"),
                "title": s.get("properties", {}).get("title"),
            }
            for s in (body.get("sheets") or [])
        ]
        return {
            "spreadsheet_id": body.get("spreadsheetId"),
            "spreadsheet_url": body.get("spreadsheetUrl"),
            "title": (body.get("properties") or {}).get("title", title),
            "sheets": sheets,
        }

    async def create_worksheet(
        self,
        tokens: TokenSet,
        *,
        spreadsheet_id: str,
        title: str,
    ) -> dict[str, Any]:
        resp = await self.http.request(
            "POST",
            f"{SHEETS_API}/{quote(spreadsheet_id, safe='')}:batchUpdate",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            json={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        )
        self._raise_for(resp)
        body = resp.body if isinstance(resp.body, dict) else {}
        replies = body.get("replies") or []
        props = (replies[0].get("addSheet") or {}).get("properties", {}) if replies else {}
        return {
            "spreadsheet_id": body.get("spreadsheetId", spreadsheet_id),
            "worksheet_id": str(props.get("sheetId")) if props.get("sheetId") is not None else None,
            "title": props.get("title", title),
        }

    async def read_rows(
        self,
        tokens: TokenSet,
        *,
        spreadsheet_id: str,
        range_name: str,
    ) -> dict[str, Any]:
        encoded_range = quote(range_name, safe="!:'")
        resp = await self.http.request(
            "GET",
            f"{SHEETS_API}/{quote(spreadsheet_id, safe='')}/values/{encoded_range}",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        self._raise_for(resp)
        body = resp.body if isinstance(resp.body, dict) else {}
        values = body.get("values") or []
        return {
            "spreadsheet_id": spreadsheet_id,
            "range": body.get("range", range_name),
            "values": values,
            "row_count": len(values),
        }

    async def append_row(
        self,
        tokens: TokenSet,
        *,
        spreadsheet_id: str,
        range_name: str,
        values: list[Any],
        value_input_option: str = "USER_ENTERED",
    ) -> dict[str, Any]:
        encoded_range = quote(range_name, safe="!:'")
        resp = await self.http.request(
            "POST",
            f"{SHEETS_API}/{quote(spreadsheet_id, safe='')}/values/{encoded_range}:append",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            params={"valueInputOption": value_input_option, "insertDataOption": "INSERT_ROWS"},
            json={"values": [values]},
        )
        self._raise_for(resp)
        body = resp.body if isinstance(resp.body, dict) else {}
        updates = body.get("updates") or {}
        return {
            "spreadsheet_id": body.get("spreadsheetId", spreadsheet_id),
            "table_range": body.get("tableRange"),
            "updated_range": updates.get("updatedRange"),
            "updated_rows": updates.get("updatedRows", 1),
        }

    async def update_row(
        self,
        tokens: TokenSet,
        *,
        spreadsheet_id: str,
        range_name: str,
        values: list[Any],
        value_input_option: str = "USER_ENTERED",
    ) -> dict[str, Any]:
        encoded_range = quote(range_name, safe="!:'")
        resp = await self.http.request(
            "PUT",
            f"{SHEETS_API}/{quote(spreadsheet_id, safe='')}/values/{encoded_range}",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            params={"valueInputOption": value_input_option},
            json={"values": [values]},
        )
        self._raise_for(resp)
        body = resp.body if isinstance(resp.body, dict) else {}
        return {
            "spreadsheet_id": body.get("spreadsheetId", spreadsheet_id),
            "updated_range": body.get("updatedRange", range_name),
            "updated_rows": body.get("updatedRows", 1),
        }

    async def search_rows(
        self,
        tokens: TokenSet,
        *,
        spreadsheet_id: str,
        range_name: str,
        query: str,
        column_index: int | None = None,
        match_exact: bool = False,
    ) -> dict[str, Any]:
        read_res = await self.read_rows(
            tokens, spreadsheet_id=spreadsheet_id, range_name=range_name
        )
        needle = query.strip().lower()
        matches: list[dict[str, Any]] = []
        for idx, row in enumerate(read_res["values"], start=1):
            cells = [row[column_index]] if (column_index is not None and column_index < len(row)) else row
            if column_index is not None and column_index >= len(row):
                continue
            hit = False
            for cell in cells:
                cell_str = str(cell).strip().lower()
                if (cell_str == needle) if match_exact else (needle in cell_str):
                    hit = True
                    break
            if hit:
                matches.append({"row_number": idx, "values": row})
        return {
            "spreadsheet_id": spreadsheet_id,
            "range": read_res["range"],
            "matches": matches,
            "match_count": len(matches),
        }
