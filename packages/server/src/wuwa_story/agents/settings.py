from decimal import Decimal
from functools import lru_cache
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_", env_file=".env", extra="ignore")
    provider: Literal["responses", "chat", "gemini"] = "responses"
    model: str = "gpt-6-luna"
    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr = SecretStr("")
    daily_budget_usd: Decimal = Field(default=Decimal(0), ge=0)
    daily_token_limit: int = Field(default=0, ge=0)
    budget_timezone: str = "Europe/Kyiv"
    input_usd_per_million: Decimal = Field(default=Decimal("0.10"), ge=0)
    cached_input_usd_per_million: Decimal | None = Field(default=None, ge=0)
    cache_write_usd_per_million: Decimal | None = Field(default=None, ge=0)
    output_usd_per_million: Decimal = Field(default=Decimal("0.50"), ge=0)
    price_safety_multiplier: Decimal = Field(default=Decimal("1.25"), ge=1)
    max_steps: int = Field(default=16, ge=1, le=100)
    max_input_tokens: int = Field(default=65536, ge=1000, le=1_000_000)
    max_output_tokens: int = Field(default=4096, ge=128, le=32000)
    context_compaction: bool = True
    compaction_threshold_ratio: float = Field(default=0.8, ge=0.25, le=0.95)
    compaction_output_tokens: int = Field(default=32000, ge=4096, le=128000)
    max_tool_calls_per_step: int = Field(default=8, ge=1, le=20)
    tool_result_chars: int = Field(default=18000, ge=1000, le=50000)
    http_timeout_seconds: float = Field(default=120, gt=0, le=600)
    rate_limit_retries: int = Field(default=3, ge=0, le=10)
    rate_limit_backoff_seconds: float = Field(default=30, ge=1, le=300)
    rate_limit_wait_seconds: float = Field(default=180, ge=1, le=600)
    max_response_bytes: int = Field(default=2_000_000, ge=10000)
    reasoning_effort: str = "medium"
    chat_token_parameter: Literal["max_tokens",
                                  "max_completion_tokens"] = "max_tokens"
    embedding_model: str = ""
    embedding_dimensions: int = Field(default=1536, ge=1, le=4096)
    embedding_provider: Literal["responses", "chat", "gemini"] | None = None
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr = SecretStr("")
    read_database_url: SecretStr = SecretStr("")
    database_schemas: str = "core,raw,graph,i18n,ontology,content,story,search,storage"
    autonomous_enabled: bool = False
    autonomous_poll_seconds: int = Field(default=60, ge=10, le=3600)
    autonomous_max_pending: int = Field(default=1, ge=1, le=10)
    embedding_input_usd_per_million: Decimal = Field(
        default=Decimal("0.02"), ge=0)
    public_query_embeddings: bool = False
    query_requests_per_hour: int = Field(default=30, ge=1, le=1000)
    vision_enabled: bool = False
    vision_frame_interval: float = Field(default=2, ge=1, le=30)
    vision_max_frames: int = Field(default=600, ge=2, le=1200)
    vision_batch_frames: int = Field(default=48, ge=1, le=64)
    vision_atlas_frames: int = Field(default=12, ge=1, le=24)
    vision_atlas_tile_width: int = Field(default=384, ge=160, le=640)
    vision_atlas_quality: int = Field(default=85, ge=60, le=95)
    vision_output_tokens: int = Field(default=8192, ge=1024, le=16000)

    @field_validator("embedding_provider", "embedding_base_url", mode="before")
    @classmethod
    def empty_embedding_override(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("budget_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    def public_config(self) -> dict[str, Any]:
        # Never checkpoint API keys. Endpoint/model/rates are pinned per job.
        return self.model_dump(
            mode="json",
            exclude={"api_key", "embedding_api_key", "read_database_url", "daily_budget_usd", "daily_token_limit",
                     "public_query_embeddings", "autonomous_enabled", "autonomous_poll_seconds", "autonomous_max_pending"},
        )

    def require_enabled(self) -> None:
        if not self.api_key.get_secret_value() or self.daily_budget_usd <= 0:
            raise ValueError(
                "Configure AGENT_API_KEY and a positive AGENT_DAILY_BUDGET_USD")
        if self.input_usd_per_million == 0 and self.output_usd_per_million == 0:
            raise ValueError(
                "Configure nonzero model prices for paid execution")


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()


_PROVIDERS = ("responses", "chat", "gemini")
_REASONING_EFFORTS = ("none", "low", "medium", "high")
_READ_SCHEMAS = (
    "core",
    "raw",
    "graph",
    "i18n",
    "ontology",
    "content",
    "story",
    "search",
    "storage",
)


def _register_agent_settings() -> None:
    from wuwa_story.config.app_settings import SettingSpec, register_settings

    register_settings([
        # Category: Agent · Budget & Limits
        SettingSpec(
            key="agent.budget.daily_budget_usd",
            category="Agent · Budget & Limits",
            name="Daily Budget (USD)",
            description="Shared daily spend cap across Story Analysis, Cutscene Vision, and embeddings.",
            value_type="decimal",
            validation={"min": 0, "max": 10000},
            fallback=lambda: get_agent_settings().daily_budget_usd,
        ),
        SettingSpec(
            key="agent.budget.daily_token_limit",
            category="Agent · Budget & Limits",
            name="Daily Token Limit",
            description="Shared daily token limit across all agent calls (0 = no token cap).",
            value_type="integer",
            validation={"min": 0, "max": 1_000_000_000},
            fallback=lambda: get_agent_settings().daily_token_limit,
        ),
        SettingSpec(
            key="agent.budget.timezone",
            category="Agent · Budget & Limits",
            name="Budget Timezone",
            description="IANA timezone determining when the daily budget counter rolls over.",
            value_type="string",
            validation={"min_length": 1, "max_length": 64, "timezone": True},
            fallback=lambda: get_agent_settings().budget_timezone,
        ),
        SettingSpec(
            key="agent.budget.price_safety_multiplier",
            category="Agent · Budget & Limits",
            name="Price Safety Multiplier",
            description="Safety buffer applied when reserving and accounting provider charges.",
            value_type="decimal",
            validation={"min": 1, "max": 5},
            fallback=lambda: get_agent_settings().price_safety_multiplier,
        ),
        SettingSpec(
            key="agent.budget.max_input_tokens",
            category="Agent · Budget & Limits",
            name="Default Max Input Tokens",
            description="Default per-request input token ceiling when mode-specific override is unset.",
            value_type="integer",
            validation={"min": 1000, "max": 1_000_000},
            fallback=lambda: get_agent_settings().max_input_tokens,
        ),
        SettingSpec(
            key="agent.budget.max_tool_calls_per_step",
            category="Agent · Budget & Limits",
            name="Max Tool Calls Per Step",
            description="Maximum parallel tool calls accepted in a single model turn.",
            value_type="integer",
            validation={"min": 1, "max": 50},
            fallback=lambda: get_agent_settings().max_tool_calls_per_step,
        ),
        # Category: Agent · Story Analysis
        SettingSpec(
            key="agent.story.provider",
            category="Agent · Story Analysis",
            name="Story Provider",
            description="API protocol used for quest & story analysis.",
            value_type="enum",
            choices=_PROVIDERS,
            fallback=lambda: get_agent_settings().provider,
        ),
        SettingSpec(
            key="agent.story.model",
            category="Agent · Story Analysis",
            name="Story Model",
            description="Model ID used specifically for Story Analysis and autonomous research.",
            value_type="string",
            validation={"min_length": 1, "max_length": 128},
            fallback=lambda: get_agent_settings().model,
        ),
        SettingSpec(
            key="agent.story.reasoning_effort",
            category="Agent · Story Analysis",
            name="Story Reasoning Effort",
            description="Reasoning depth requested for Story Analysis turns.",
            value_type="enum",
            choices=_REASONING_EFFORTS,
            fallback=lambda: get_agent_settings().reasoning_effort,
        ),
        SettingSpec(
            key="agent.story.max_steps",
            category="Agent · Story Analysis",
            name="Story Max Steps",
            description="Maximum model turns per Story Analysis job before pausing.",
            value_type="integer",
            validation={"min": 1, "max": 100},
            fallback=lambda: get_agent_settings().max_steps,
        ),
        SettingSpec(
            key="agent.story.max_input_tokens",
            category="Agent · Story Analysis",
            name="Story Max Input Tokens",
            description="Per-step context token ceiling specifically for Story Analysis.",
            value_type="integer",
            validation={"min": 1000, "max": 1_000_000},
            fallback=lambda: get_agent_settings().max_input_tokens,
        ),
        SettingSpec(
            key="agent.story.max_output_tokens",
            category="Agent · Story Analysis",
            name="Story Max Output Tokens",
            description="Maximum output tokens per Story Analysis response.",
            value_type="integer",
            validation={"min": 128, "max": 32000},
            fallback=lambda: get_agent_settings().max_output_tokens,
        ),
        SettingSpec(
            key="agent.story.input_usd_per_million",
            category="Agent · Story Analysis",
            name="Story Input $/1M Tokens",
            description="Input token rate used to budget Story Analysis runs.",
            value_type="decimal",
            validation={"min": 0, "max": 1000},
            fallback=lambda: get_agent_settings().input_usd_per_million,
        ),
        SettingSpec(
            key="agent.story.output_usd_per_million",
            category="Agent · Story Analysis",
            name="Story Output $/1M Tokens",
            description="Output token rate used to budget Story Analysis runs.",
            value_type="decimal",
            validation={"min": 0, "max": 1000},
            fallback=lambda: get_agent_settings().output_usd_per_million,
        ),
        SettingSpec(
            key="agent.story.autonomous_enabled",
            category="Agent · Story Analysis",
            name="Autonomous Story Queue",
            description="Allow the background Story Agent worker to pick up autonomous work items.",
            value_type="boolean",
            fallback=lambda: get_agent_settings().autonomous_enabled,
        ),
        SettingSpec(
            key="agent.story.database_schemas",
            category="Agent · Story Analysis",
            name="Read-Only Query Schemas",
            description="Database schemas exposed to the Story Agent read-only SQL tools.",
            value_type="list",
            multi=True,
            choices=_READ_SCHEMAS,
            validation={"min_items": 1},
            fallback=lambda: [
                s.strip()
                for s in get_agent_settings().database_schemas.split(",")
                if s.strip()
            ],
        ),
        # Category: Agent · Cutscene Vision
        SettingSpec(
            key="agent.cutscene.enabled",
            category="Agent · Cutscene Vision",
            name="Cutscene Vision Enabled",
            description="Master toggle allowing Cutscene Vision jobs to enqueue and execute.",
            value_type="boolean",
            fallback=lambda: get_agent_settings().vision_enabled,
        ),
        SettingSpec(
            key="agent.cutscene.provider",
            category="Agent · Cutscene Vision",
            name="Cutscene Vision Provider",
            description="API protocol used specifically for Cutscene Vision analysis.",
            value_type="enum",
            choices=_PROVIDERS,
            fallback=lambda: get_agent_settings().provider,
        ),
        SettingSpec(
            key="agent.cutscene.model",
            category="Agent · Cutscene Vision",
            name="Cutscene Vision Model",
            description="Multimodal model ID used specifically for Cutscene Vision analysis.",
            value_type="string",
            validation={"min_length": 1, "max_length": 128},
            fallback=lambda: get_agent_settings().model,
        ),
        SettingSpec(
            key="agent.cutscene.reasoning_effort",
            category="Agent · Cutscene Vision",
            name="Cutscene Reasoning Effort",
            description="Reasoning depth requested for Cutscene Vision turns.",
            value_type="enum",
            choices=_REASONING_EFFORTS,
            default="low",
        ),
        SettingSpec(
            key="agent.cutscene.max_input_tokens",
            category="Agent · Cutscene Vision",
            name="Cutscene Max Input Tokens",
            description="Per-step context token ceiling specifically for Cutscene Vision turns.",
            value_type="integer",
            validation={"min": 1000, "max": 1_000_000},
            fallback=lambda: get_agent_settings().max_input_tokens,
        ),
        SettingSpec(
            key="agent.cutscene.output_tokens",
            category="Agent · Cutscene Vision",
            name="Cutscene Output Tokens",
            description="Maximum output tokens reserved for Cutscene Vision turns.",
            value_type="integer",
            validation={"min": 1024, "max": 32000},
            fallback=lambda: get_agent_settings().vision_output_tokens,
        ),
        SettingSpec(
            key="agent.cutscene.max_frames",
            category="Agent · Cutscene Vision",
            name="Cutscene Max Frames",
            description="Upper frame budget for a cutscene video workspace.",
            value_type="integer",
            validation={"min": 2, "max": 1200},
            fallback=lambda: get_agent_settings().vision_max_frames,
        ),
        SettingSpec(
            key="agent.cutscene.frame_interval",
            category="Agent · Cutscene Vision",
            name="Cutscene Base Frame Interval (s)",
            description="Target seconds between overview timeline frames.",
            value_type="number",
            validation={"min": 1, "max": 30},
            fallback=lambda: get_agent_settings().vision_frame_interval,
        ),
        SettingSpec(
            key="agent.cutscene.atlas_frames",
            category="Agent · Cutscene Vision",
            name="Frames Per Atlas",
            description="Number of frame tiles packed into each generated atlas sheet.",
            value_type="integer",
            validation={"min": 1, "max": 24},
            fallback=lambda: get_agent_settings().vision_atlas_frames,
        ),
        SettingSpec(
            key="agent.cutscene.input_usd_per_million",
            category="Agent · Cutscene Vision",
            name="Cutscene Input $/1M Tokens",
            description="Input token rate used to budget Cutscene Vision runs.",
            value_type="decimal",
            validation={"min": 0, "max": 1000},
            fallback=lambda: get_agent_settings().input_usd_per_million,
        ),
        SettingSpec(
            key="agent.cutscene.output_usd_per_million",
            category="Agent · Cutscene Vision",
            name="Cutscene Output $/1M Tokens",
            description="Output token rate used to budget Cutscene Vision runs.",
            value_type="decimal",
            validation={"min": 0, "max": 1000},
            fallback=lambda: get_agent_settings().output_usd_per_million,
        ),
        # Category: Agent · Credentials
        SettingSpec(
            key="agent.provider.base_url",
            category="Agent · Credentials",
            name="Provider Base URL",
            description="Base HTTP endpoint shared by Story Analysis and Cutscene Vision unless isolated.",
            value_type="string",
            validation={"min_length": 1, "max_length": 256},
            fallback=lambda: get_agent_settings().base_url,
        ),
        SettingSpec(
            key="agent.provider.api_key",
            category="Agent · Credentials",
            name="Provider API Key",
            description="Secret credential overriding AGENT_API_KEY when set (never displayed in UI).",
            value_type="secret",
            is_secret=True,
            validation={"min_length": 1, "max_length": 512},
            fallback=lambda: get_agent_settings().api_key.get_secret_value(),
        ),
    ])


_register_agent_settings()


async def agent_db_override_fields(
    session: Any, *, scope: Literal["default", "story", "cutscene"] = "default"
) -> dict[str, Any]:
    """Return ONLY settings explicitly overridden in ops.app_setting mapped to AgentSettings fields."""
    from wuwa_story.config.app_settings import (
        decode_stored_value,
        get_setting_spec,
        load_app_settings_rows,
    )

    key_map: dict[str, str] = {
        "agent.budget.daily_budget_usd": "daily_budget_usd",
        "agent.budget.daily_token_limit": "daily_token_limit",
        "agent.budget.timezone": "budget_timezone",
        "agent.budget.price_safety_multiplier": "price_safety_multiplier",
        "agent.budget.max_input_tokens": "max_input_tokens",
        "agent.budget.max_tool_calls_per_step": "max_tool_calls_per_step",
        "agent.provider.base_url": "base_url",
        "agent.provider.api_key": "api_key",
        "agent.cutscene.enabled": "vision_enabled",
    }
    if scope == "story":
        key_map.update({
            "agent.story.provider": "provider",
            "agent.story.model": "model",
            "agent.story.reasoning_effort": "reasoning_effort",
            "agent.story.max_steps": "max_steps",
            "agent.story.max_input_tokens": "max_input_tokens",
            "agent.story.max_output_tokens": "max_output_tokens",
            "agent.story.input_usd_per_million": "input_usd_per_million",
            "agent.story.output_usd_per_million": "output_usd_per_million",
            "agent.story.autonomous_enabled": "autonomous_enabled",
            "agent.story.database_schemas": "database_schemas",
        })
    elif scope == "cutscene":
        key_map.update({
            "agent.cutscene.provider": "provider",
            "agent.cutscene.model": "model",
            "agent.cutscene.reasoning_effort": "reasoning_effort",
            "agent.cutscene.max_input_tokens": "max_input_tokens",
            "agent.cutscene.output_tokens": "vision_output_tokens",
            "agent.cutscene.max_frames": "vision_max_frames",
            "agent.cutscene.frame_interval": "vision_frame_interval",
            "agent.cutscene.atlas_frames": "vision_atlas_frames",
            "agent.cutscene.input_usd_per_million": "input_usd_per_million",
            "agent.cutscene.output_usd_per_million": "output_usd_per_million",
        })

    rows = await load_app_settings_rows(session, list(key_map.keys()))
    updates: dict[str, Any] = {}
    for setting_key, field_name in key_map.items():
        row = rows.get(setting_key)
        if row is None or row.value is None:
            continue
        spec = get_setting_spec(setting_key)
        if spec is None:
            continue
        typed = decode_stored_value(spec, row.value)
        if setting_key == "agent.provider.api_key":
            typed = SecretStr(str(typed))
        elif setting_key == "agent.story.database_schemas" and isinstance(typed, list):
            typed = ",".join(typed)
        updates[field_name] = typed
    return updates


async def load_effective_agent_settings(
    session: Any,
    base: AgentSettings | None = None,
    *,
    scope: Literal["default", "story", "cutscene"] = "default",
) -> AgentSettings:
    """Resolve effective AgentSettings with ops.app_setting overrides layered over env."""
    resolved = base or get_agent_settings()
    updates = await agent_db_override_fields(session, scope=scope)
    if scope == "cutscene" and "reasoning_effort" not in updates:
        updates["reasoning_effort"] = "low"
    if not updates or not hasattr(resolved, "model_copy"):
        return resolved
    return resolved.model_copy(update=updates)
