from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


def _resolve_path(path: str, context: dict[str, Any]) -> Any:
    parts = path.split(".")
    curr: Any = context
    for part in parts:
        if isinstance(curr, dict) and part in curr:
            curr = curr[part]
        else:
            return None
    return curr


def render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        full = _PLACEHOLDER_RE.fullmatch(value)
        if full:
            resolved = _resolve_path(full.group(1), context)
            return value if resolved is None else resolved

        def _repl(match: re.Match[str]) -> str:
            resolved = _resolve_path(match.group(1), context)
            return "" if resolved is None else str(resolved)

        return _PLACEHOLDER_RE.sub(_repl, value)

    if isinstance(value, dict):
        return {k: render_value(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, context) for v in value]
    return value


def render_params(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Recursively substitute {{node_id.field}} placeholders in action parameters."""
    return {k: render_value(v, context) for k, v in (params or {}).items()}
