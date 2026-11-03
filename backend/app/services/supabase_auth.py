from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings


class SupabaseAuthError(Exception):
    def __init__(self, status_code: int, message: str = "Supabase Auth request failed") -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class SupabaseAuthService:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.supabase_url or not settings.supabase_anon_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be configured")
        self.base_url = settings.supabase_url.rstrip("/") + "/auth/v1"
        self.anon_key = settings.supabase_anon_key

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        access_token: str | None = None,
        query: str = "",
    ) -> dict[str, Any]:
        headers = {"apikey": self.anon_key, "Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        url = f"{self.base_url}/{path}{query}"
        try:
            response = httpx.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=httpx.Timeout(15.0, connect=5.0),
            )
        except httpx.RequestError as exc:
            raise SupabaseAuthError(503, "Authentication provider is unavailable") from exc

        if response.is_error:
            message = "Supabase Auth request failed"
            try:
                body = response.json()
                if isinstance(body, dict):
                    message = str(
                        body.get("msg")
                        or body.get("message")
                        or body.get("error_description")
                        or body.get("error")
                        or message
                    )
            except ValueError:
                pass
            raise SupabaseAuthError(response.status_code, message)

        if response.status_code == 204 or not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise SupabaseAuthError(502, "Invalid response from authentication provider") from exc
        if not isinstance(data, dict):
            raise SupabaseAuthError(502, "Invalid response from authentication provider")
        return data

    def signup(self, email: str, password: str, full_name: str | None) -> dict[str, Any]:
        user_data = {"full_name": full_name} if full_name else {}
        return self._request(
            "POST",
            "signup",
            payload={"email": email, "password": password, "data": user_data},
        )

    def login(self, email: str, password: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "token",
            query="?grant_type=password",
            payload={"email": email, "password": password},
        )

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "token",
            query="?grant_type=refresh_token",
            payload={"refresh_token": refresh_token},
        )

    def request_password_reset(self, email: str) -> dict[str, Any]:
        redirect_url = get_settings().supabase_password_reset_redirect_url
        return self._request(
            "POST",
            "recover",
            query="?" + urlencode({"redirect_to": redirect_url}),
            payload={"email": email},
        )

    def update_password(self, access_token: str, new_password: str) -> dict[str, Any]:
        return self._request(
            "PUT",
            "user",
            payload={"password": new_password},
            access_token=access_token,
        )

    def logout(self, access_token: str) -> None:
        self._request("POST", "logout", access_token=access_token)
