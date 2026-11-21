"""Tests for the conftest.py hard real-DB protection guard."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.conftest import assert_safe_test_db_urls


def test_guard_rejects_missing_test_urls_and_never_falls_back() -> None:
    with pytest.raises(RuntimeError, match="never fall back to DATABASE_URL"):
        assert_safe_test_db_urls("", "")


def test_guard_rejects_remote_supabase_host() -> None:
    supa = "postgresql+psycopg://postgres:pw@aws-0-eu-west-1.pooler.supabase.com:5432/opsmind_test"
    supa_app = "postgresql+psycopg://opsmind_app:pw@aws-0-eu-west-1.pooler.supabase.com:5432/opsmind_test"
    with pytest.raises(RuntimeError, match="is not allowed"):
        assert_safe_test_db_urls(supa, supa_app)


def test_guard_rejects_non_opsmind_test_db_name() -> None:
    wrong_db = "postgresql+psycopg://postgres:postgres@127.0.0.1:55432/postgres"
    wrong_app = "postgresql+psycopg://opsmind_app:opsmind_app@127.0.0.1:55432/postgres"
    with pytest.raises(RuntimeError, match="must be 'opsmind_test'"):
        assert_safe_test_db_urls(wrong_db, wrong_app)


def test_guard_rejects_url_matching_real_database_url() -> None:
    url = "postgresql+psycopg://postgres:pw@127.0.0.1:55432/opsmind_test"
    app_url = "postgresql+psycopg://opsmind_app:pw@127.0.0.1:55432/opsmind_test"
    with pytest.raises(RuntimeError, match="same database as the real"):
        assert_safe_test_db_urls(url, app_url, real_db_url=url, real_app_db_url=app_url)


def test_guard_allows_localhost_and_127_opsmind_test() -> None:
    for host in ("localhost", "127.0.0.1"):
        assert_safe_test_db_urls(
            f"postgresql+psycopg://postgres:postgres@{host}:55432/opsmind_test",
            f"postgresql+psycopg://opsmind_app:opsmind_app@{host}:55432/opsmind_test",
            real_db_url="postgresql+psycopg://postgres:pw@aws-0-eu-west-1.pooler.supabase.com:5432/postgres",
            real_app_db_url="postgresql+psycopg://opsmind_app:pw@aws-0-eu-west-1.pooler.supabase.com:5432/postgres",
        )


def test_pytest_session_aborts_on_remote_host_without_running_tests() -> None:
    env = os.environ.copy()
    env["TEST_DATABASE_URL"] = "postgresql+psycopg://postgres:pw@db.example.supabase.co:5432/postgres"
    env["TEST_APP_DATABASE_URL"] = "postgresql+psycopg://opsmind_app:pw@db.example.supabase.co:5432/postgres"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_conftest_guard.py", "-k", "allows_localhost", "-q"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    combined = proc.stdout + proc.stderr
    assert "REFUSING TO RUN TESTS" in combined
    assert "db.example.supabase.co" in combined
