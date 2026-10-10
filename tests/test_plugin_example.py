import pytest
from pydantic import ValidationError

from docs.plugins import example_config
from docs.plugins.example_config import ExamplePluginSettings


def test_example_plugin_has_validated_off_by_default_settings(monkeypatch):
    defaults = ExamplePluginSettings(_env_file=None)
    assert defaults.enabled is False
    assert defaults.cooldown_seconds == 3
    assert defaults.max_per_hour == 10

    monkeypatch.setenv("ALING_EXAMPLE_ENABLED", "true")
    monkeypatch.setenv("ALING_EXAMPLE_COOLDOWN_SECONDS", "5")
    configured = ExamplePluginSettings(_env_file=None)
    assert configured.enabled is True
    assert configured.cooldown_seconds == 5

    with pytest.raises(ValidationError):
        ExamplePluginSettings(_env_file=None, max_per_hour=0)


def test_example_plugin_rate_limiter_uses_user_scope_and_settings():
    settings = ExamplePluginSettings(_env_file=None, cooldown_seconds=3, max_per_hour=1)
    create_limiter = getattr(example_config, "create_rate_limiter", None)
    assert callable(create_limiter)
    limiter = create_limiter(settings)

    assert limiter.allow(12)
    assert not limiter.allow(12)
    assert limiter.allow(13)
