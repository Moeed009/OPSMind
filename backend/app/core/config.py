from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "OpsMind"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    frontend_url: str = "http://localhost:5173"

    # Both URLs point to the same Supabase PostgreSQL database.
    database_url: str = ""
    app_database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"

    # Supabase Auth configuration. The anon/publishable key is safe for client-style
    # Auth endpoints; never put the service_role key in this application config.
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_password_reset_redirect_url: str = "http://localhost:5173/reset-password"

    auth_access_cookie_name: str = "opsmind_access_token"
    auth_refresh_cookie_name: str = "opsmind_refresh_token"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"


@lru_cache
def get_settings() -> Settings:
    return Settings()
