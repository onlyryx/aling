import sqlite3

import pytest
from pydantic import ValidationError

from aling.core.config import Settings
from aling.core.permissions import PermissionContext, require_group_admin
from aling.core.rate_limit import RateLimiter
from aling.core.storage import GroupSettingsStore


def test_configuration_rejects_missing_token_and_incomplete_ai():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, onebot_access_token="  ")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, onebot_access_token="test", ai_enabled=True)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, onebot_access_token="test", ai_timeout=0)
    settings = Settings(_env_file=None, onebot_access_token="test", ai_api_key="secret-test")
    assert "secret-test" not in repr(settings)
    assert settings.proactive_enabled is False


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_group_admin_and_superuser_authorization(role):
    require_group_admin(PermissionContext(12, 34, role), set(), set())
    require_group_admin(PermissionContext(12, 34, "member"), {12}, {34})


@pytest.mark.parametrize(
    "context,superusers,groups",
    [
        (PermissionContext(12, 34, "member"), set(), set()),
        (PermissionContext(12, 34, "admin"), set(), {56}),
        (PermissionContext(12, None, "admin"), {12}, set()),
    ],
)
def test_unauthorized_or_out_of_scope_management_is_rejected(context, superusers, groups):
    with pytest.raises(PermissionError):
        require_group_admin(context, superusers, groups)


def test_rate_limit_enforces_cooldown_and_window_per_group():
    now = [0.0]
    limiter = RateLimiter(cooldown=5, limit=2, window=60, clock=lambda: now[0])
    assert limiter.allow(1)
    assert not limiter.allow(1)
    assert limiter.allow(2)
    now[0] = 5
    assert limiter.allow(1)
    now[0] = 10
    assert not limiter.allow(1)
    now[0] = 60
    assert limiter.allow(1)


def test_cooldown_longer_than_window_is_not_reset_by_window_expiry():
    now = [0.0]
    limiter = RateLimiter(cooldown=120, limit=1, window=60, clock=lambda: now[0])
    assert limiter.allow(1)
    now[0] = 60
    assert not limiter.allow(1)
    now[0] = 100
    assert not limiter.allow(1)
    now[0] = 120
    assert limiter.allow(1)


async def test_sqlite_persists_only_group_preferences(tmp_path):
    path = tmp_path / "nested" / "settings.db"
    store = GroupSettingsStore(path)
    await store.initialize()
    assert not await store.is_proactive_enabled(34)
    await store.set_proactive_enabled(34, True)
    reopened = GroupSettingsStore(path)
    assert await reopened.is_proactive_enabled(34)
    await reopened.set_proactive_enabled(34, False)
    assert not await store.is_proactive_enabled(34)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("group_settings",)
        ]
        assert conn.execute("SELECT * FROM group_settings").fetchall() == [(34, 0)]
