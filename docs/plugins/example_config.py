"""Configuration owned by the documentation example plugin."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from aling.core.rate_limit import RateLimiter


class ExamplePluginSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ALING_EXAMPLE_", extra="ignore")

    enabled: bool = False
    cooldown_seconds: float = Field(default=3, ge=0, le=3600)
    max_per_hour: int = Field(default=10, ge=1, le=100)


def create_rate_limiter(settings: ExamplePluginSettings) -> RateLimiter:
    return RateLimiter(cooldown=settings.cooldown_seconds, limit=settings.max_per_hour)
