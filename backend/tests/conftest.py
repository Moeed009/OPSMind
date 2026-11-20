"""Hard pytest safety guard and isolated test-DB wiring.

At pytest session start (before any test runs or touches a database):
- Requires TEST_DATABASE_URL and TEST_APP_DATABASE_URL (NEVER falls back to DATABASE_URL).
- Host MUST be localhost or 127.0.0.1.
- Database name MUST be 'opsmind_test'.
- Must NOT match the real DATABASE_URL / APP_DATABASE_URL in backend/.env.
If any check fails, pytest aborts immediately with a clear error message.
"""
from __future__ import annotations

import os
import pathlib
from urllib.parse import urlparse

import pytest

ALLOWED_TEST_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1"})
REQUIRED_TEST_DB_NAME = "opsmind_test"


def _parse_db_target(url: str) -> tuple[str, int, str]:
    cleaned = (url or "").strip()
    if not cleaned:
        return ("", 0, "")
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
) -> None:
    """Enforce that test DB URLs are explicitly provided, local-only, and target opsmind_test."""
    if not (test_db_url or "").strip() or not (test_app_db_url or "").strip():
        raise RuntimeError(
            "REFUSING TO RUN TESTS: TEST_DATABASE_URL and TEST_APP_DATABASE_URL must both be set "
            f"(host in {sorted(ALLOWED_TEST_HOSTS)}, database '{REQUIRED_TEST_DB_NAME}'). "
            "Tests never fall back to DATABASE_URL."
        )

    real_targets = {
        t for t in (_parse_db_target(real_db_url), _parse_db_target(real_app_db_url)) if t[0]
    }
    for label, url in (
        ("TEST_DATABASE_URL", test_db_url),
        ("TEST_APP_DATABASE_URL", test_app_db_url),
    ):
        target = _parse_db_target(url)
        host, _port, dbname = target
        if not host:
            raise RuntimeError(f"REFUSING TO RUN TESTS: {label} is not a valid database URL.")
        if host not in ALLOWED_TEST_HOSTS:
            raise RuntimeError(
                f"REFUSING TO RUN TESTS: {label} host '{host}' is not allowed. "
                f"Only {sorted(ALLOWED_TEST_HOSTS)} are permitted."
            )
        if dbname != REQUIRED_TEST_DB_NAME:
            raise RuntimeError(
                f"REFUSING TO RUN TESTS: {label} database '{dbname}' is not allowed. "
                f"Database name must be '{REQUIRED_TEST_DB_NAME}'."
            )
        if target in real_targets:
            raise RuntimeError(
                f"REFUSING TO RUN TESTS: {label} points to the same database as the real "
                "DATABASE_URL / APP_DATABASE_URL in .env."
            )


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    real_db_url, real_app_db_url = _read_dotenv_urls()
    test_db_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    test_app_db_url = os.environ.get("TEST_APP_DATABASE_URL", "").strip()

    try:
        assert_safe_test_db_urls(
            test_db_url,
            test_app_db_url,
            real_db_url=real_db_url,
            real_app_db_url=real_app_db_url,
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
