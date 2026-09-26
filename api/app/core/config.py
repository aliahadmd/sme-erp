"""Application settings — every value comes from the environment (.env in dev)."""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ERP API"
    environment: str = "dev"
    api_prefix: str = "/api"

    # --- infrastructure (swappable for external managed services) ---
    database_url: str
    redis_url: str
    s3_endpoint: str = "http://seaweedfs:8333"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "erp-dev"
    s3_region: str = "us-east-1"

    # --- auth ---
    jwt_secret: str = "dev-secret-change-me"
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    # --- AI / OpenRouter (empty key = AI features disabled) ---
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"

    # --- bootstrap admin (used by `make seed`) ---
    admin_email: str = "admin@example.com"
    admin_password: str = "admin123"
    admin_full_name: str = "Admin"

    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        # Accept "http://a,http://b" as well as JSON-array syntax.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _production_safety(self) -> "Settings":
        """Fail fast on insecure defaults outside dev — the app must not boot
        with a known/forgotten default secret in staging or production."""
        if self.environment != "dev" and (
            self.jwt_secret == "dev-secret-change-me" or len(self.jwt_secret) < 32
        ):
            raise ValueError(
                "JWT_SECRET must be overridden with at least 32 characters "
                "when ENVIRONMENT is not 'dev'"
            )
        return self

    @property
    def ai_enabled(self) -> bool:
        return bool(self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
