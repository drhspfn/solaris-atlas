from decimal import Decimal
from functools import lru_cache
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")
    provider: Literal["responses", "chat", "gemini"] = "responses"
    model: str = "gpt-6-luna"
    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr = SecretStr("")
    daily_budget_usd: Decimal = Field(default=Decimal(0), ge=0)
    daily_token_limit: int = Field(default=2_000_000, ge=1)
    budget_timezone: str = "Europe/Moscow"
    input_usd_per_million: Decimal = Field(default=Decimal("0.10"), ge=0)
    output_usd_per_million: Decimal = Field(default=Decimal("0.50"), ge=0)
    price_safety_multiplier: Decimal = Field(default=Decimal("1.25"), ge=1)
    max_steps: int = Field(default=16, ge=1, le=100)
    max_input_tokens: int = Field(default=65536, ge=1000, le=250000)
    max_output_tokens: int = Field(default=4096, ge=128, le=32000)
    max_tool_calls_per_step: int = Field(default=8, ge=1, le=20)
    tool_result_chars: int = Field(default=18000, ge=1000, le=50000)
    http_timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_response_bytes: int = Field(default=2_000_000, ge=10000)
    reasoning_effort: str = "medium"
    chat_token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    embedding_model: str = ""
    embedding_dimensions: int = Field(default=1536, ge=1, le=4096)
    embedding_input_usd_per_million: Decimal = Field(default=Decimal("0.02"), ge=0)
    public_query_embeddings: bool = False
    query_requests_per_hour: int = Field(default=30, ge=1, le=1000)

    @field_validator("budget_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    def public_config(self) -> dict[str, Any]:
        # Never checkpoint API keys. Endpoint/model/rates are pinned per job.
        return self.model_dump(
            mode="json",
            exclude={"api_key", "daily_budget_usd", "daily_token_limit", "public_query_embeddings"},
        )

    def require_enabled(self) -> None:
        if not self.api_key.get_secret_value() or self.daily_budget_usd <= 0:
            raise ValueError("Configure AGENT_API_KEY and a positive AGENT_DAILY_BUDGET_USD")
        if self.input_usd_per_million == 0 and self.output_usd_per_million == 0:
            raise ValueError("Configure nonzero model prices for paid execution")


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
