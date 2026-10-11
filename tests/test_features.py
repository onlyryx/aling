import pytest

from aling.core.config import Settings
from aling.core.permissions import PermissionContext
from aling.core.storage import GroupSettingsStore
from aling.services.features import FeatureDisabledError, FeatureService


async def test_feature_requires_group_opt_in_within_global_allowance(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        allowed_groups={34},
        superusers={99},
    )
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)
    await features.initialize()

    assert not await features.enabled(34, "web")
    assert not await features.enabled(56, "web")
    await features.set_global(99, "web", True)
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


async def test_superuser_can_toggle_persistent_global_gate_without_env_ceiling(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        superusers={99},
    )
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)
    await features.initialize()

    assert not features.globally_enabled("web")
    with pytest.raises(PermissionError):
        await features.set_global(12, "web", False)
    await features.set_global(99, "web", False)
    assert not features.globally_enabled("web")

    restarted = FeatureService(settings, store)
    await restarted.initialize()
    assert not restarted.globally_enabled("web")
    await restarted.set_global(99, "web", True)
    assert restarted.globally_enabled("web")

    await restarted.set_global(99, "meme", True)
    assert restarted.globally_enabled("meme")


async def test_ai_global_toggle_requires_credentials_but_not_legacy_env_switch(tmp_path):
    settings = Settings(_env_file=None, onebot_access_token="test", superusers={12})
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)
    await features.initialize()

    with pytest.raises(FeatureDisabledError, match="AI_API_KEY.*AI_MODEL"):
        await features.set_global(12, "ai", True)
    with pytest.raises(FeatureDisabledError, match="AI_API_KEY.*AI_MODEL"):
        await features.set_global(12, "memory", True)

    settings.ai_api_key = "fake-test-key"
    settings.ai_model = "test-model"
    with pytest.raises(FeatureDisabledError, match="global AI switch"):
        await features.set_global(12, "memory", True)
    await features.set_global(12, "ai", True)
    assert features.globally_enabled("ai")
    await features.set_global(12, "memory", True)
    assert features.globally_enabled("memory")


async def test_memory_feature_needs_global_ai_and_confirmed_group_opt_in(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        ai_api_key="fake-test-key",
        ai_model="test-model",
        allowed_groups={34},
        superusers={12},
    )
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)
    context = PermissionContext(12, 34, "admin")
    await features.initialize()

    assert not await features.enabled(34, "memory")
    await features.set_global(12, "ai", True)
    await features.set_global(12, "memory", True)
    await features.request_memory(context, scope="qq")
    await features.confirm_memory(context, scope="qq")
    assert not await features.enabled(34, "memory")
    await features.set(context, "ai", True)
    assert await features.enabled(34, "memory")
    await features.set_global(12, "ai", False)
    assert not features.globally_enabled("memory")
    assert not await features.enabled(34, "memory")
    await features.set_global(12, "ai", True)
    assert await features.enabled(34, "memory")
