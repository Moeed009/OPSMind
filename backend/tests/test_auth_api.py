from fastapi.testclient import TestClient

from app.api.v1 import auth as auth_api
from app.core.config import Settings
from app.main import app

client = TestClient(app)


class FakeSupabaseAuth:
    def signup(self, email: str, password: str, full_name: str | None):
        return {
            "user": {
                "id": "a2e9a1f4-3e0c-4c14-9e5b-2d31c7f4e101",
                "email": email,
                "user_metadata": {"full_name": full_name},
            },
            "access_token": "access-token-value-that-is-long-enough-for-cookie-test",
            "refresh_token": "refresh-token-value-that-is-long-enough-for-cookie-test",
            "expires_in": 3600,
        }

    def login(self, email: str, password: str):
        return {
            "user": {
                "id": "a2e9a1f4-3e0c-4c14-9e5b-2d31c7f4e101",
                "email": email,
                "user_metadata": {"full_name": "Test User"},
            },
            "access_token": "access-token-value-that-is-long-enough-for-cookie-test",
            "refresh_token": "refresh-token-value-that-is-long-enough-for-cookie-test",
            "expires_in": 3600,
        }

    def request_password_reset(self, email: str):
        return {}


def configure_auth_mocks(monkeypatch):
    monkeypatch.setattr(auth_api, "SupabaseAuthService", FakeSupabaseAuth)
    monkeypatch.setattr(auth_api, "_sync_local_user", lambda user: None)
    monkeypatch.setattr(
        auth_api,
        "get_settings",
        lambda: Settings(
            auth_cookie_secure=False,
            auth_cookie_samesite="lax",
            auth_access_cookie_name="opsmind_access_token",
            auth_refresh_cookie_name="opsmind_refresh_token",
        ),
    )


def test_signup_creates_session_cookies(monkeypatch):
    configure_auth_mocks(monkeypatch)
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "new.user@example.com",
            "password": "StrongPassword123",
            "full_name": "New User",
        },
    )

    assert response.status_code == 201
    assert response.json()["confirmation_required"] is False
    assert response.json()["user"]["email"] == "new.user@example.com"
    assert "opsmind_access_token" in response.cookies
    assert "opsmind_refresh_token" in response.cookies
    assert "access_token" not in response.json()


def test_signup_rejects_weak_password():
    response = client.post(
        "/api/v1/auth/signup",
        json={"email": "new.user@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_login_creates_session_cookies(monkeypatch):
    configure_auth_mocks(monkeypatch)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "test.user@example.com", "password": "StrongPassword123"},
    )

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["user"]["id"] == "a2e9a1f4-3e0c-4c14-9e5b-2d31c7f4e101"
    assert "opsmind_access_token" in response.cookies


def test_password_reset_request_does_not_disclose_account_existence(monkeypatch):
    configure_auth_mocks(monkeypatch)
    response = client.post(
        "/api/v1/auth/password/reset-request",
        json={"email": "unknown@example.com"},
    )

    assert response.status_code == 202
    assert "If an account exists" in response.json()["message"]
