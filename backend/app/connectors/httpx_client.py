"""Production HttpClient adapter. Requires: pip install httpx"""
from __future__ import annotations

import httpx

from .http import HttpResponse


class HttpxClient:
    def __init__(self, timeout: float = 20.0):
        self._client = httpx.AsyncClient(timeout=timeout)

    async def request(
        self, method: str, url: str, *,
        headers: dict[str, str] | None = None,
        params: dict | None = None,
        form: dict | None = None,
        json: dict | list | None = None,
    ) -> HttpResponse:
        r = await self._client.request(method, url, headers=headers, params=params, data=form, json=json)
        try:
            body = r.json() if r.content else {}
        except ValueError:
            body = {}
        return HttpResponse(r.status_code, body if isinstance(body, (dict, list)) else {}, dict(r.headers))

    async def aclose(self) -> None:
        await self._client.aclose()
