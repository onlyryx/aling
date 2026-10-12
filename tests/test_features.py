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


async def test_online_meme_revision_changes_for_global_and_group_toggles(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        ai_api_key="fictional",
        ai_model="test",
        allowed_groups={34},
        superusers={99},
    )
    store = GroupSettingsStore(tmp_path / "settings.db", superusers={99})
    await store.initialize()
    await store.set_search_consent(99, True)
    features = FeatureService(settings, store)
    await features.initialize()
    for feature in ("ai", "meme", "online_meme"):
        await features.set_global(99, feature, True)
    owner = PermissionContext(99, 34, "owner")
    await features.set(owner, "online_meme", True)

    start = features.online_meme_revision(34)
    await features.set(owner, "online_meme", False)
    await features.set(owner, "online_meme", True)
    after_group_toggle = features.online_meme_revision(34)
    await features.set_global(99, "online_meme", False)
    await features.set_global(99, "online_meme", True)
    after_global_toggle = features.online_meme_revision(34)

    assert after_group_toggle == (start[0], start[1] + 2)
    assert after_global_toggle == (start[0] + 2, after_group_toggle[1])


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


@pytest.fixture
async def online_features(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        superusers={12},
        allowed_groups={34},
        ai_api_key="fake-test-key",
        ai_model="test-model",
        ai_tools_enabled=True,
    )
    store = GroupSettingsStore(tmp_path / "settings.db", superusers=settings.superusers)
    await store.initialize()
    features = FeatureService(settings, store)
    await features.initialize()
    return features


@pytest.mark.parametrize("feature", ["search", "online_meme"])
async def test_online_features_default_off_and_require_owner_grant(online_features, feature):
    features = online_features
    context = PermissionContext(13, 34, "admin")
    assert not features.globally_enabled(feature)
    assert not await features.enabled(34, feature)
    with pytest.raises(FeatureDisabledError):
        await features.set(context, feature, True)
    with pytest.raises(PermissionError):
        await features.set(PermissionContext(13, 34, "member"), feature, False)
    with pytest.raises(PermissionError):
        await features.set_global(13, feature, True)
    await features.set(context, feature, False)


async def enable_online_feature(features, feature):
    context = PermissionContext(12, 34, "admin")
    await features.store.set_search_consent(12, True)
    for name in ("ai", "meme", feature):
        await features.set_global(12, name, True)
        await features.set(context, name, True)
    return context


@pytest.mark.parametrize("feature", ["search", "online_meme"])
async def test_online_feature_requires_effective_ai_and_reports_exact_blocker(
    online_features, feature
):
    features = online_features
    context = await enable_online_feature(features, feature)
    assert await features.enabled(34, feature)
    assert not await features.enabled(56, feature)
    await features.set(context, "ai", False)
    assert (await features.state(34, feature)).blocked_by == ["group_ai_off"]
    await features.set(context, "ai", True)
    await features.set_global(12, "ai", False)
    assert (await features.state(34, feature)).blocked_by == ["global_ai_off"]
    assert not features.globally_enabled(feature)
    await features.set_global(12, "ai", True)
    features.settings.ai_api_key = ""
    assert (await features.state(34, feature)).blocked_by == ["ai_credentials_missing"]
    features.settings.ai_api_key = "fake-test-key"
    await features.set_global(12, feature, False)
    assert (await features.state(34, feature)).blocked_by == ["global_off"]


async def test_search_is_independent_of_web_and_ai_tools_switch(online_features):
    features = online_features
    await enable_online_feature(features, "search")
    assert not await features.enabled(34, "web")
    await features.set_ai_tools_enabled(12, False)
    assert await features.enabled(34, "search")


async def test_online_meme_requires_meme_but_not_web_or_search(online_features):
    features = online_features
    context = await enable_online_feature(features, "online_meme")
    assert not await features.enabled(34, "search")
    assert not await features.enabled(34, "web")
    await features.set(context, "meme", False)
    assert (await features.state(34, "online_meme")).blocked_by == ["group_meme_off"]
    await features.set(context, "meme", True)
    await features.set_global(12, "meme", False)
    assert (await features.state(34, "online_meme")).blocked_by == ["global_meme_off"]


async def test_ai_tools_service_is_owner_only_and_persists_beyond_environment(online_features):
    features = online_features
    assert await features.ai_tools_enabled()
    with pytest.raises(PermissionError):
        await features.set_ai_tools_enabled(13, False)
    await features.set_ai_tools_enabled(12, False)
    restarted = FeatureService(features.settings, features.store)
    await restarted.initialize()
    assert not await restarted.ai_tools_enabled()
    await restarted.set_ai_tools_enabled(12, True)
    restarted.settings.ai_tools_enabled = False
    await restarted.initialize()
    assert await restarted.ai_tools_enabled()
