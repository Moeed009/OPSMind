"""Tests for the conftest.py real-DB protection guard (F-08)."""
from __future__ import annotations

import pytest

from tests.conftest import assert_safe_test_db_urls


def test_guard_rejects_missing_test_urls() -> None:
    with pytest.raises(RuntimeError, match="must both be set"):
        assert_safe_test_db_urls("", "")


def test_guard_rejects_url_matching_real_database_url() -> None:
    real = "postgresql+psycopg://postgres:pw@127.0.0.1:5432/prod_db"
    real_app = "postgresql+psycopg://opsmind_app:pw@127.0.0.1:5432/prod_db"
    with pytest.raises(RuntimeError, match="same database as the real"):
        assert_safe_test_db_urls(real, real_app, real_db_url=real, real_app_db_url=real_app)


def test_guard_rejects_supabase_host_by_default() -> None:
    supa = "postgresql+psycopg://postgres:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"
    supa_app = "postgresql+psycopg://opsmind_app:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"
    with pytest.raises(RuntimeError, match="remote Supabase host"):
        assert_safe_test_db_urls(supa, supa_app)


def test_guard_allows_isolated_local_test_db() -> None:
    assert_safe_test_db_urls(
        "postgresql+psycopg://postgres:postgres@127.0.0.1:55432/opsmind_test",
        "postgresql+psycopg://opsmind_app:opsmind_app@127.0.0.1:55432/opsmind_test",
        real_db_url="postgresql+psycopg://postgres:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres",
        real_app_db_url="postgresql+psycopg://opsmind_app:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres",
    )
