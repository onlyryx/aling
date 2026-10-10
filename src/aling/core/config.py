from datetime import time
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "INFO"
    onebot_access_token: SecretStr
    superusers: set[int] = Field(default_factory=set)
    allowed_groups: set[int] = Field(default_factory=set)
    database_path: Path = Path("data/ryxbot.db")
    extra_plugins: list[str] = Field(default_factory=list)

    ai_api_key: SecretStr = SecretStr("")
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = ""
    ai_timeout: float = Field(default=30, gt=0, le=120)
    ai_max_input_chars: int = Field(default=2000, ge=1, le=10000)
    ai_max_output_chars: int = Field(default=1500, ge=1, le=4000)
    ai_max_tokens: int = Field(default=512, ge=1, le=4096)
    ai_cooldown_seconds: float = Field(default=3, ge=0)
    ai_max_per_hour: int = Field(default=20, ge=1)
    ai_max_concurrency: int = Field(default=2, ge=1, le=16)
    ai_system_prompt: str = Field(
        default_factory=lambda: (
            files("aling").joinpath("prompts/default.txt").read_text(encoding="utf-8").strip()
        )
    )

    proactive_enabled: bool = False
    proactive_cooldown_seconds: float = Field(default=300, gt=0)
    proactive_max_per_hour: int = Field(default=3, ge=1)
    proactive_max_per_day: int = Field(default=1, ge=1, le=24)
    proactive_timezone: str = "Asia/Shanghai"
    proactive_quiet_start: time = time(23, 0)
    proactive_quiet_end: time = time(8, 0)
    ai_tools_enabled: bool = False
    ai_tool_timeout: float = Field(default=10, gt=0, le=30)
    ai_tool_turn_timeout: float = Field(default=45, gt=0, le=120)
    ai_session_ttl: int = Field(default=600, ge=60, le=3600)
    ai_session_max_turns: int = Field(default=6, ge=1, le=20)
    ai_session_max_chars: int = Field(default=12000, ge=1000, le=50000)
    ai_session_max_count: int = Field(default=1000, ge=1, le=10000)
    meme_dir: Path = Path("data/memes")
    web_timeout: float = Field(default=10, gt=0, le=60)
    web_max_bytes: int = Field(default=1_000_000, ge=1, le=5_000_000)

    @field_validator("proactive_timezone")
    @classmethod
    def valid_proactive_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Invalid proactive timezone") from exc
        return value

    @model_validator(mode="after")
    def valid_proactive_quiet_hours(self) -> "Settings":
        if self.proactive_quiet_start == self.proactive_quiet_end:
            raise ValueError("Proactive quiet hours must have different start and end times")
        return self

    @field_validator("onebot_access_token")
    @classmethod
    def token_required(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if not token.strip() or token != token.strip():
            raise ValueError("OneBot access token must be nonblank without surrounding whitespace")
        return value

    @field_validator("log_level")
    @classmethod
    def valid_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Invalid log level")
        return value

    @field_validator("ai_base_url")
    @classmethod
    def valid_api_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Expected an HTTP(S) API base URL without credentials/query/fragment")
        return value.rstrip("/")

    @property
    def ai_configured(self) -> bool:
        api_key = (
            self.ai_api_key.get_secret_value()
            if isinstance(self.ai_api_key, SecretStr)
            else str(self.ai_api_key)
        )
        return bool(api_key.strip() and self.ai_model.strip())

    def group_allowed(self, group_id: int) -> bool:
        return not self.allowed_groups or group_id in self.allowed_groups
