import asyncio
import sqlite3
import threading

import pytest

from aling.core.config import Settings
from aling.core.permissions import PermissionContext
from aling.runtime import Runtime
from aling.services import features as feature_errors


@pytest.fixture
async def consent_runtime(tmp_path):
    runtime = Runtime.create(
        Settings(
            _env_file=None,
            onebot_access_token="fictional-token",
            ai_api_key="fictional-model-key",
            ai_model="fictional-model",
            superusers={12},
            allowed_groups={34},
            database_path=tmp_path / "settings.db",
            meme_dir=tmp_path / "memes",
        )
    )
    await runtime.startup()
    await runtime.features.set_global(12, "ai", True)
    await runtime.features.set_global(12, "memory", True)
    await runtime.features.set(PermissionContext(12, 34, "member"), "ai", True)
    yield runtime
    await runtime.shutdown()


async def test_common_gate_requires_consent_and_clears_sessions(consent_runtime):
    features, sessions = consent_runtime.features, consent_runtime.sessions
    context = PermissionContext(12, 34, "member")
    with pytest.raises(feature_errors.MemoryConfirmationRequired):
        await features.set(context, "memory", True)
    confirmation = await features.request_memory(context, scope="webui:session-a")
    assert confirmation.expires_in == 60
    assert "发送给模型服务" in confirmation.notice
    await features.confirm_memory(context, scope="webui:session-a", ticket=confirmation.ticket)
    assert await features.enabled(34, "memory")
    await sessions.add(34, 12, "fictional question", "fictional reply")
    await features.set_global(12, "ai", False)
    assert await sessions.get(34, 12) == []
    assert not await features.enabled(34, "memory")


async def test_memory_confirmation_is_single_use_and_webui_requires_ticket(consent_runtime):
    features = consent_runtime.features
    context = PermissionContext(12, 34, "member")
    confirmation = await features.request_memory(context, scope="webui:session-a")
    with pytest.raises(feature_errors.MemoryConfirmationRequired):
        await features.confirm_memory(context, scope="webui:session-a")
    assert not await features.enabled(34, "memory")
    await features.confirm_memory(context, scope="webui:session-a", ticket=confirmation.ticket)
    with pytest.raises(feature_errors.MemoryConfirmationRequired):
        await features.confirm_memory(context, scope="webui:session-a", ticket=confirmation.ticket)


@pytest.mark.parametrize("elapsed", [60, 61])
async def test_confirmation_expires_without_enabling_memory(consent_runtime, elapsed):
    features = consent_runtime.features
    now = [0.0]
    features.clock = lambda: now[0]
    context = PermissionContext(12, 34, "member")
    confirmation = await features.request_memory(context, scope="webui:session-a")
    now[0] = elapsed
    with pytest.raises(feature_errors.MemoryConfirmationExpired):
        await features.confirm_memory(context, scope="webui:session-a", ticket=confirmation.ticket)
    assert not await features.enabled(34, "memory")


@pytest.mark.parametrize(
    ("context", "scope"),
    [
        (PermissionContext(12, 56, "member"), "webui:session-a"),
        (PermissionContext(13, 34, "admin"), "webui:session-a"),
        (PermissionContext(12, 34, "member"), "webui:session-b"),
        (PermissionContext(12, 34, "member"), "qq"),
    ],
)
async def test_confirmation_is_bound_to_group_identity_and_scope(consent_runtime, context, scope):
    features = consent_runtime.features
    consent_runtime.settings.allowed_groups.add(56)
    original = PermissionContext(12, 34, "member")
    confirmation = await features.request_memory(original, scope="webui:session-a")
    with pytest.raises((PermissionError, feature_errors.MemoryConfirmationRequired)):
        await features.confirm_memory(context, scope=scope, ticket=confirmation.ticket)
    assert not await features.enabled(34, "memory")
    await features.confirm_memory(original, scope="webui:session-a", ticket=confirmation.ticket)
    assert await features.enabled(34, "memory")


@pytest.mark.parametrize("global_change", [False, True])
@pytest.mark.parametrize("feature", ["ai", "memory"])
async def test_disable_clears_sessions_revokes_tickets_and_blocks_late_writes(
    consent_runtime, global_change, feature
):
    features, sessions = consent_runtime.features, consent_runtime.sessions
    context = PermissionContext(12, 34, "member")
    confirmation = await features.request_memory(context, scope="qq")
    await sessions.add(34, 12, "question", "reply")
    await sessions.add(56, 12, "other question", "other reply")
    revision = sessions.revision
    if global_change:
        await features.set_global(12, feature, False)
        await features.set_global(12, feature, True)
    else:
        await features.set(context, feature, False)
        if feature == "ai":
            await features.set(context, feature, True)
    assert await sessions.get(34, 12) == []
    assert bool(await sessions.get(56, 12)) is not global_change
    await sessions.add(34, 12, "late question", "late reply", expected_revision=revision)
    assert await sessions.get(34, 12) == []
    with pytest.raises(feature_errors.MemoryConfirmationRequired):
        await features.confirm_memory(context, scope="qq", ticket=confirmation.ticket)


async def test_qq_uses_latest_confirmation_and_confirmation_rechecks_permissions(consent_runtime):
    features = consent_runtime.features
    context = PermissionContext(13, 34, "admin")
    first = await features.request_memory(context, scope="qq")
    second = await features.request_memory(context, scope="qq")
    assert first.ticket != second.ticket
    with pytest.raises(feature_errors.MemoryConfirmationRequired):
        await features.confirm_memory(context, scope="qq", ticket=first.ticket)
    with pytest.raises(PermissionError):
        await features.confirm_memory(PermissionContext(13, 34, "member"), scope="qq")
    assert not await features.enabled(34, "memory")
    await features.confirm_memory(context, scope="qq")
    assert await features.enabled(34, "memory")


@pytest.mark.parametrize(
    ("context", "enabled", "exception"),
    [
        (PermissionContext(13, 34, "member"), False, PermissionError),
        (PermissionContext(12, 56, "member"), False, PermissionError),
        (PermissionContext(12, 34, "member"), 1, ValueError),
        (PermissionContext(12, 34, "member"), "false", ValueError),
    ],
)
async def test_invalid_group_change_does_not_modify_database(
    consent_runtime, context, enabled, exception
):
    features = consent_runtime.features
    before = consent_runtime.store.path.read_bytes()
    with pytest.raises(exception):
        await features.set(context, "ai", enabled)
    assert consent_runtime.store.path.read_bytes() == before
    assert await features.enabled(34, "ai")


async def test_confirmation_records_are_bounded_and_expired_records_are_purged(consent_runtime):
    features = consent_runtime.features
    context = PermissionContext(12, 34, "member")
    now = [0.0]
    features.clock = lambda: now[0]
    first = await features.request_memory(context, scope="webui:first")
    for number in range(1000):
        await features.request_memory(context, scope=f"webui:{number}")
    with pytest.raises(feature_errors.MemoryConfirmationRequired):
        await features.confirm_memory(context, scope="webui:first", ticket=first.ticket)
    now[0] = 61
    latest = await features.request_memory(context, scope="webui:new")
    await features.confirm_memory(context, scope="webui:new", ticket=latest.ticket)
    assert await features.enabled(34, "memory")


@pytest.mark.parametrize("global_change", [False, True])
async def test_failed_save_preserves_state_sessions_and_pending_consent(
    consent_runtime, monkeypatch, global_change
):
    features, sessions = consent_runtime.features, consent_runtime.sessions
    context = PermissionContext(12, 34, "member")
    confirmation = await features.request_memory(context, scope="qq")
    await sessions.add(34, 12, "question", "reply")

    async def fail_save(*args):
        raise OSError("fictional persistence failure")

    method = "set_global_feature_enabled" if global_change else "set_feature_enabled"
    with monkeypatch.context() as patch:
        patch.setattr(consent_runtime.store, method, fail_save)
        with pytest.raises(OSError):
            if global_change:
                await features.set_global(12, "ai", False)
            else:
                await features.set(context, "ai", False)
    assert await features.enabled(34, "ai")
    assert await sessions.get(34, 12)
    await features.confirm_memory(context, scope="qq", ticket=confirmation.ticket)
    assert await features.enabled(34, "memory")


async def test_concurrent_successful_changes_publish_in_saved_order(consent_runtime, monkeypatch):
    features = consent_runtime.features
    entered, release = asyncio.Event(), asyncio.Event()
    save = consent_runtime.store.set_global_feature_enabled

    async def paused_save(feature, enabled):
        if not enabled:
            entered.set()
            await release.wait()
        await save(feature, enabled)

    monkeypatch.setattr(consent_runtime.store, "set_global_feature_enabled", paused_save)
    disabling = asyncio.create_task(features.set_global(12, "ai", False))
    await entered.wait()
    enabling = asyncio.create_task(features.set_global(12, "ai", True))
    release.set()
    await asyncio.gather(disabling, enabling)
    restarted = feature_errors.FeatureService(consent_runtime.settings, consent_runtime.store)
    await restarted.initialize()
    assert features.globally_enabled("ai") and restarted.globally_enabled("ai")


async def test_feature_state_separates_requests_and_dependency_reasons(consent_runtime):
    features = consent_runtime.features
    context = PermissionContext(12, 34, "member")
    await features.request_memory(context, scope="qq")
    await features.confirm_memory(context, scope="qq")
    await features.set(context, "ai", False)
    state = await features.state(34, "memory")
    assert state.global_requested and state.group_requested and not state.effective
    assert state.blocked_by == ["group_ai_off"]
    await features.set_global(12, "ai", False)
    assert (await features.state(34, "memory")).blocked_by == ["global_ai_off", "group_ai_off"]
    assert (await features.state(None, "memory")).group_requested is None
    assert "scope_restricted" in (await features.state(56, "memory")).blocked_by
    consent_runtime.settings.ai_api_key = ""
    assert (await features.state(34, "ai")).blocked_by == [
        "global_off",
        "ai_credentials_missing",
        "group_off",
    ]


async def test_confirmation_rechecks_credentials_and_preserves_ticket_on_failed_save(
    consent_runtime, monkeypatch
):
    features = consent_runtime.features
    context = PermissionContext(12, 34, "member")
    confirmation = await features.request_memory(context, scope="qq")
    consent_runtime.settings.ai_api_key = ""
    with pytest.raises(feature_errors.FeatureDisabledError):
        await features.confirm_memory(context, scope="qq", ticket=confirmation.ticket)
    consent_runtime.settings.ai_api_key = "fictional-model-key"

    async def fail_save(*args):
        raise OSError("fictional persistence failure")

    with monkeypatch.context() as patch:
        patch.setattr(consent_runtime.store, "set_feature_enabled", fail_save)
        with pytest.raises(OSError):
            await features.confirm_memory(context, scope="qq", ticket=confirmation.ticket)
    assert not await features.enabled(34, "memory")
    await features.confirm_memory(context, scope="qq", ticket=confirmation.ticket)
    assert await features.enabled(34, "memory")


@pytest.fixture
def paused_sqlite_write(consent_runtime, monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    connect = sqlite3.connect
    first = True

    class ObservedConnection(sqlite3.Connection):
        def close(self):
            super().close()
            finished.set()

    def paused_connect(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release the paused SQLite write")
            kwargs["factory"] = ObservedConnection
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", paused_connect)
    yield entered, release, finished
    release.set()


@pytest.mark.parametrize("operation", ["global", "group", "confirm"])
async def test_cancelled_sqlite_save_finishes_before_a_queued_mutation(
    consent_runtime, paused_sqlite_write, operation
):
    features, sessions = consent_runtime.features, consent_runtime.sessions
    entered, release, finished = paused_sqlite_write
    context = PermissionContext(12, 34, "member")
    confirmation = await features.request_memory(context, scope="qq")
    await sessions.add(34, 12, "question", "reply")
    revision = sessions.revision

    def change(enabled):
        if operation == "global":
            return features.set_global(12, "ai", enabled)
        if operation == "group":
            return features.set(context, "ai", enabled)
        return features.confirm_memory(context, scope="qq", ticket=confirmation.ticket)

    saving = asyncio.create_task(change(False))
    following = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        saving.cancel()
        await asyncio.sleep(0)
        saving.cancel()
        following = asyncio.create_task(change(True))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        serialized = not saving.done() and not following.done()
    finally:
        release.set()
        tasks = [saving] if following is None else [saving, following]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 2)
    assert serialized, "Cancellation released serialization before the SQLite write completed"
    assert isinstance(outcomes[0], asyncio.CancelledError)
    if operation == "confirm":
        assert isinstance(outcomes[1], feature_errors.MemoryConfirmationRequired)
        assert await features.enabled(34, "memory")
    else:
        assert outcomes[1] is None
        assert await features.enabled(34, "ai")
        assert await sessions.get(34, 12) == []
        await sessions.add(34, 12, "late question", "reply", expected_revision=revision)
        assert await sessions.get(34, 12) == []
    with pytest.raises(feature_errors.MemoryConfirmationRequired):
        await features.confirm_memory(context, scope="qq", ticket=confirmation.ticket)


@pytest.mark.parametrize("global_change", [False, True])
async def test_cancellation_during_cleanup_retains_serialization(
    consent_runtime, monkeypatch, global_change
):
    features, sessions = consent_runtime.features, consent_runtime.sessions
    context = PermissionContext(12, 34, "member")
    confirmation = await features.request_memory(context, scope="qq")
    await sessions.add(34, 12, "question", "reply")
    entered, release = asyncio.Event(), asyncio.Event()
    method = "clear" if global_change else "clear_group"
    clear = getattr(sessions, method)

    async def paused_cleanup(*args):
        entered.set()
        await release.wait()
        await clear(*args)

    monkeypatch.setattr(sessions, method, paused_cleanup)

    def change(enabled):
        if global_change:
            return features.set_global(12, "ai", enabled)
        return features.set(context, "ai", enabled)

    disabling = asyncio.create_task(change(False))
    following = None
    try:
        await entered.wait()
        disabling.cancel()
        await asyncio.sleep(0)
        following = asyncio.create_task(change(True))
        await asyncio.sleep(0)
        serialized = not disabling.done() and not following.done()
    finally:
        release.set()
        tasks = [disabling] if following is None else [disabling, following]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    assert serialized, "Cancellation released serialization before cleanup completed"
    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert outcomes[1] is None
    assert await features.enabled(34, "ai")
    assert await sessions.get(34, 12) == []
    with pytest.raises(feature_errors.MemoryConfirmationRequired):
        await features.confirm_memory(context, scope="qq", ticket=confirmation.ticket)
