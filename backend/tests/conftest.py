"""Pytest safety guard and isolated test-DB wiring (F-08).

Refuses to run the test suite if TEST_DATABASE_URL / TEST_APP_DATABASE_URL is missing,
matches the real .env DATABASE_URL / APP_DATABASE_URL, or targets a production Supabase host.
"""
from __future__ import annotations

import os
import pathlib
from urllib.parse import urlparse

import pytest

_ALLOWED_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres", "db"})
_BLOCKED_HOST_SUFFIXES = (".supabase.com", ".supabase.co", "supabase.com", "supabase.co")


def _parse_db_target(url: str) -> tuple[str, int, str]:
    cleaned = (url or "").strip()
    if not cleaned:
        return ("", 0, "")
    # Normalize SQLAlchemy dialect prefix (postgresql+psycopg:// -> postgresql://)
    if "://" in cleaned:
        scheme, rest = cleaned.split("://", 1)
        cleaned = f"{scheme.split('+')[0]}://{rest}"
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    port = parsed.port or 5432
    dbname = (parsed.path or "").lstrip("/").lower()
    return (host, port, dbname)


def _read_dotenv_urls() -> tuple[str, str]:
    """Read real DATABASE_URL and APP_DATABASE_URL from backend/.env without logging values."""
    env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return ("", "")
    real_db = ""
    real_app = ""
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip().strip('"').strip("'")
        if key == "DATABASE_URL":
            real_db = val
        elif key == "APP_DATABASE_URL":
            real_app = val
    return (real_db, real_app)


def assert_safe_test_db_urls(
    test_db_url: str,
    test_app_db_url: str,
    *,
    real_db_url: str = "",
    real_app_db_url: str = "",
    allow_remote: bool = False,
) -> None:
    """Validate that test DB URLs are configured, isolated from real DB, and not pointing at Supabase."""
    if not test_db_url or not test_app_db_url:
        raise RuntimeError(
            "REFUSING TO RUN TESTS: TEST_DATABASE_URL and TEST_APP_DATABASE_URL must both be set "
            "to an isolated test Postgres database (never the real DB)."
        )

    real_targets = {
        t for t in (_parse_db_target(real_db_url), _parse_db_target(real_app_db_url)) if t[0]
    }
    for label, url in (
        ("TEST_DATABASE_URL", test_db_url),
        ("TEST_APP_DATABASE_URL", test_app_db_url),
    ):
        target = _parse_db_target(url)
        host = target[0]
        if not host:
            raise RuntimeError(f"REFUSING TO RUN TESTS: {label} is not a valid database URL.")
        if target in real_targets:
            raise RuntimeError(
                f"REFUSING TO RUN TESTS: {label} points to the same database as the real "
                "DATABASE_URL / APP_DATABASE_URL in .env."
            )
        if any(host.endswith(sfx) for sfx in _BLOCKED_HOST_SUFFIXES) and not allow_remote:
            raise RuntimeError(
                f"REFUSING TO RUN TESTS: {label} targets a remote Supabase host. "
                "Use a local Docker Postgres test DB or set OPSMIND_ALLOW_REMOTE_TEST_DB=1 "
                "with a dedicated Supabase branch DB (different from .env DATABASE_URL)."
            )
        if host not in _ALLOWED_LOCAL_HOSTS and not allow_remote:
            raise RuntimeError(
                f"REFUSING TO RUN TESTS: {label} targets non-local host '{host}'. "
                "Use local Postgres or explicitly opt in with OPSMIND_ALLOW_REMOTE_TEST_DB=1 "
                "against a dedicated branch DB."
            )


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    real_db_url, real_app_db_url = _read_dotenv_urls()
    test_db_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    test_app_db_url = os.environ.get("TEST_APP_DATABASE_URL", "").strip()

    # In CI (GitHub Actions), DATABASE_URL / APP_DATABASE_URL point to the ephemeral local Postgres service.
    if not test_db_url and os.environ.get("CI", "").lower() == "true":
        test_db_url = os.environ.get("DATABASE_URL", "").strip()
        test_app_db_url = os.environ.get("APP_DATABASE_URL", "").strip()

    allow_remote = os.environ.get("OPSMIND_ALLOW_REMOTE_TEST_DB", "").strip() == "1"
    try:
        assert_safe_test_db_urls(
            test_db_url,
            test_app_db_url,
            real_db_url=real_db_url,
            real_app_db_url=real_app_db_url,
            allow_remote=allow_remote,
        )
    except RuntimeError as exc:
        pytest.exit(str(exc), returncode=2)

    os.environ["DATABASE_URL"] = test_db_url
    os.environ["APP_DATABASE_URL"] = test_app_db_url

    from app.core.config import get_settings
    from app.db import session as db_session_mod

    get_settings.cache_clear()
    if hasattr(db_session_mod, "get_engine") and hasattr(db_session_mod.get_engine, "cache_clear"):
        db_session_mod.get_engine.cache_clear()
    if hasattr(db_session_mod, "get_session_factory") and hasattr(
        db_session_mod.get_session_factory, "cache_clear"
    ):
        db_session_mod.get_session_factory.cache_clear()
