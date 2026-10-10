from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

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

    ai_enabled: bool = False
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
    web_timeout: float = Field(default=10, gt=0, le=60)
    web_max_bytes: int = Field(default=1_000_000, ge=1, le=5_000_000)

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

    @model_validator(mode="after")
    def enabled_ai_requires_credentials(self) -> "Settings":
        if self.ai_enabled and (
            not self.ai_api_key.get_secret_value().strip() or not self.ai_model.strip()
        ):
            raise ValueError("AI_ENABLED requires AI_API_KEY and AI_MODEL")
        return self

    def group_allowed(self, group_id: int) -> bool:
        return not self.allowed_groups or group_id in self.allowed_groups
