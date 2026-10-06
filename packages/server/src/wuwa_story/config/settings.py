from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "WuWa Story Platform"
    api_url: str = "http://localhost:8000"
    database_url: str = "postgresql+asyncpg://wuwa:wuwa@localhost:5432/wuwa_story"
    s3_endpoint_url: str = "http://localhost:9000"
    # Complete bucket root; a CDN custom domain does not include the bucket name.
    media_public_base_url: str | None = None
    media_cache_control: str = "public, max-age=31536000, immutable"
    redis_url: SecretStr | None = None
    api_cache_namespace: str = "solaris-api-v1"
    api_cache_ttl_seconds: int = Field(default=3600, ge=1)
    api_cache_max_body_bytes: int = Field(default=8 * 1024 * 1024, ge=1)
    api_cache_timeout_seconds: float = Field(default=0.3, gt=0)
    s3_access_key_id: str = "minio"
    s3_secret_access_key: SecretStr = Field(default=SecretStr("minioadmin"))
    s3_bucket: str = "wuwa"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    local_storage_root: str = "./var/objects"
    log_level: str = "INFO"
    rabbitmq_url: SecretStr = Field(default=SecretStr("amqp://wuwa:wuwa@localhost:5672/"))
    auth_session_ttl_days: int = 30
    auth_pending_registration_ttl_minutes: int = 15
    auth_pending_link_ttl_minutes: int = 10
    auth_cookie_name: str = "solaris_session"
    auth_cookie_secure: bool | None = None
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_cookie_domain: str | None = None
    auth_csrf_cookie_name: str = "solaris_csrf"
    auth_csrf_secret: SecretStr = Field(default=SecretStr("development-only-change-me"))
    cors_allowed_origins: str = "http://localhost:3000,http://localhost:5173"
    frontend_url: str = "http://localhost:3000"
    google_client_id: str = ""
    google_client_secret: SecretStr = Field(default=SecretStr(""))
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    @field_validator("auth_cookie_domain", mode="before")
    @classmethod
    def blank_cookie_domain_is_host_only(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_auth_settings(self) -> "Settings":
        if self.auth_session_ttl_days < 1:
            raise ValueError("AUTH_SESSION_TTL_DAYS must be positive")
        if self.auth_pending_registration_ttl_minutes < 1 or self.auth_pending_link_ttl_minutes < 1:
            raise ValueError("Pending authentication lifetimes must be positive")
        if self.app_env == "production":
            if self.auth_csrf_secret.get_secret_value() == "development-only-change-me":
                raise ValueError("AUTH_CSRF_SECRET must be configured in production")
            if not self.auth_cookie_is_secure:
                raise ValueError("AUTH_COOKIE_SECURE must be true in production")
        return self

    @property
    def auth_cookie_is_secure(self) -> bool:
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.app_env == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
