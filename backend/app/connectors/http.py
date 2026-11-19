
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class HttpResponse:
    status: int
    body: Any = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

class HttpClient(Protocol):
    async def request(
        self, method: str, url: str, *,
        headers: dict[str, str] | None = None,
        params: dict | None = None,
        form: dict | None = None,
        json: dict | list | None = None,
    ) -> HttpResponse: ...
