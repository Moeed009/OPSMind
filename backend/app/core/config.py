from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "backend/.env"), extra="ignore")

    app_name: str = "OpsMind"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = ""
    frontend_url: str = "https://localhost:5173"

    database_url: str = ""
    app_database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_cv_bucket: str = "hr-cvs"
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_password_reset_redirect_url: str = "https://localhost:5173/reset-password"

    auth_access_cookie_name: str = "opsmind_access_token"
    auth_refresh_cookie_name: str = "opsmind_refresh_token"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

   
    connector_encryption_keys: str = ""  
    connector_encryption_current_key_id: str = ""
    oauth_state_secret: str = ""
    oauth_redirect_base_url: str = "http://localhost:8000"
    slack_client_id: str = ""
    slack_client_secret: str = ""
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""
    stripe_client_id: str = ""
    stripe_client_secret: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    zoho_client_id: str = ""
    zoho_client_secret: str = ""
    composio_api_key: str = ""

    ai_default_provider: Literal["gemini", "groq", "offline"] = "gemini"
    ai_primary_provider: Literal["groq", "gemini"] = "groq"
    ai_fallback_provider: Literal["groq", "gemini"] = "gemini"
    ai_fast_model: str = "gemini-3.5-flash-lite"
    ai_pro_model: str = "gemini-3.5-flash"
    groq_model: str = ""
    gemini_model: str = ""
    groq_fast_model: str = "openai/gpt-oss-20b"
    groq_pro_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    groq_api_key: str = ""
    ai_request_timeout_seconds: float = 30.0
    ai_max_input_chars: int = 12000
    ai_max_output_tokens: int = 1024
    groq_rate_limit_rpm: int = 30
    gemini_rate_limit_rpm: int = 15
    ai_max_retries: int = 3
    ai_circuit_breaker_threshold: int = 5
    ai_daily_token_limit: int = 500_000
    ai_monthly_token_limit: int = 5_000_000
    ai_circuit_breaker_cooldown_seconds: float = 60.0
    ai_allow_offline_fallback: bool = True
    ai_free_tier_mode: bool = True
    ai_default_monthly_budget_usd: float = 5.0
    ai_confidence_threshold: float = 0.85
    ai_redact_outbound_pii: bool = True
    retrieval_min_rank: float = 0.0
    retrieval_default_top_k: int = 5
    retrieval_max_top_k: int = 20
    retrieval_max_lexemes: int = 32
    retrieval_vector_min_similarity: float = 0.35
    retrieval_rrf_k: int = 60
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 384
    embedding_timeout_seconds: float = 15.0
    embedding_max_retries: int = 3
    embedding_batch_size: int = 32

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@opsmind.internal"
    smtp_from_name: str = "OpsMind"
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False

    @model_validator(mode="after")
    def _require_secure_cookies_in_production(self) -> "Settings":
        import os

        if self.composio_api_key and not os.environ.get("COMPOSIO_API_KEY"):
            os.environ["COMPOSIO_API_KEY"] = self.composio_api_key
        if self.environment.lower() == "production" and not self.auth_cookie_secure:
            raise ValueError("AUTH_COOKIE_SECURE must be true when ENVIRONMENT=production")
        if self.ai_request_timeout_seconds <= 0:
            raise ValueError("AI_REQUEST_TIMEOUT_SECONDS must be positive")
        if self.ai_max_retries < 0:
            raise ValueError("AI_MAX_RETRIES cannot be negative")
        if not (0.0 <= self.ai_confidence_threshold <= 1.0):
            raise ValueError("AI_CONFIDENCE_THRESHOLD must be between 0.0 and 1.0")
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()
