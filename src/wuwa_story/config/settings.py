from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "WuWa Story Platform"
    database_url: str = "postgresql+asyncpg://wuwa:wuwa@localhost:5432/wuwa_story"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = "minio"
    s3_secret_access_key: SecretStr = Field(default=SecretStr("minioadmin"))
    s3_bucket: str = "wuwa"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    local_storage_root: str = "./var/objects"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
