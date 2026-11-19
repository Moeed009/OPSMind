"""Provider name -> Connector subclass registry, populated via @register("provider")."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .errors import UnknownProvider

T = TypeVar("T", bound=type)
_REGISTRY: dict[str, type] = {}

def register(provider: str) -> Callable[[T], T]:
    def deco(cls: T) -> T:
        if provider in _REGISTRY and _REGISTRY[provider] is not cls:
            raise ValueError(f"provider '{provider}' already registered")
        cls.provider = provider  # type: ignore[attr-defined]
        _REGISTRY[provider] = cls
        return cls

    return deco

def get_connector_class(provider: str) -> type:
    try:
        return _REGISTRY[provider]
    except KeyError:
        raise UnknownProvider(f"no connector registered for '{provider}'") from None

def list_providers() -> list[str]:
    return sorted(_REGISTRY)

def _clear_for_tests() -> None:
    _REGISTRY.clear()
