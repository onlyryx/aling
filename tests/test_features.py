import pytest

from aling.core.config import Settings
from aling.core.permissions import PermissionContext
from aling.core.storage import GroupSettingsStore
from aling.services.features import FeatureDisabledError, FeatureService


async def test_feature_requires_group_opt_in_within_global_allowance(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        web_enabled=True,
        allowed_groups={34},
    )
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)

    assert not await features.enabled(34, "web")
    assert not await features.enabled(56, "web")
    await features.set(PermissionContext(12, 34, "admin"), "web", True)
    assert await features.enabled(34, "web")
    await features.set(PermissionContext(12, 34, "admin"), "web", False)
    assert not await features.enabled(34, "web")
    await features.set(PermissionContext(12, 34, "owner"), "web", True)
    assert await features.enabled(34, "web")


async def test_feature_setting_requires_admin_and_cannot_enable_global_off(tmp_path):
    settings = Settings(_env_file=None, onebot_access_token="test")
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)

    with pytest.raises(PermissionError):
        await features.set(PermissionContext(12, 34, "member"), "web", True)
    with pytest.raises(FeatureDisabledError):
        await features.set(PermissionContext(12, 34, "admin"), "web", True)
    await features.set(PermissionContext(12, 34, "admin"), "web", False)
    assert not await features.enabled(34, "web")


async def test_deployer_can_toggle_persistent_global_gate_but_not_exceed_env(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        web_enabled=True,
        superusers={99},
    )
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)
    await features.initialize()

    assert features.globally_enabled("web")
    with pytest.raises(PermissionError):
        await features.set_global(12, "web", False)
    await features.set_global(99, "web", False)
    assert not features.globally_enabled("web")

    restarted = FeatureService(settings, store)
    await restarted.initialize()
    assert not restarted.globally_enabled("web")
    await restarted.set_global(99, "web", True)
    assert restarted.globally_enabled("web")

    settings.web_enabled = False
    await store.set_global_feature_enabled("web", True)
    capped = FeatureService(settings, store)
    await capped.initialize()
    assert not capped.globally_enabled("web")
    with pytest.raises(FeatureDisabledError):
        await capped.set_global(99, "web", True)


async def test_memory_feature_requires_global_ai_and_memory_opt_in(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        ai_enabled=True,
        ai_api_key="fake-test-key",
        ai_model="test-model",
        ai_memory_enabled=True,
        allowed_groups={34},
        superusers={12},
    )
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)
    context = PermissionContext(12, 34, "admin")

    assert not await features.enabled(34, "memory")
    await features.set(context, "memory", True)
    assert not await features.enabled(34, "memory")
    await features.set(context, "ai", True)
    assert await features.enabled(34, "memory")
    await features.set_global(12, "ai", False)
    assert not features.globally_enabled("memory")
    assert not await features.enabled(34, "memory")
    await features.set_global(12, "ai", True)
    assert await features.enabled(34, "memory")
    settings.ai_memory_enabled = False
    assert not await features.enabled(34, "memory")
