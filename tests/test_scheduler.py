from datetime import UTC, datetime

import pytest

from aling.core.config import Settings
from aling.core.permissions import PermissionContext
from aling.core.storage import GroupSettingsStore
from aling.services.proactive import ProactiveSender
from aling.services.scheduler import ProactiveScheduler


def settings(**kwargs):
    return Settings(_env_file=None, onebot_access_token="test", **kwargs)


async def setup_scheduler(tmp_path, **config):
    values = {
        "proactive_enabled": True,
        "proactive_timezone": "Asia/Shanghai",
        "proactive_quiet_start": "23:00",
        "proactive_quiet_end": "08:00",
        "allowed_groups": {34},
        **config,
    }
    options = settings(**values)
    store = GroupSettingsStore(tmp_path / "schedule.db")
    await store.initialize()
    await store.set_proactive_enabled(34, True)
    await store.set_schedule(34, 12, 0, "scheduled hello", owner_id=12)
    sender = ProactiveSender(options, store)
    scheduler = ProactiveScheduler(options, store, sender)
    return options, store, scheduler


async def test_scheduler_sends_only_once_when_due_within_grace(tmp_path):
    options, store, scheduler = await setup_scheduler(tmp_path)
    sent = []

    async def send(group_id, text):
        sent.append((group_id, text))

    now = datetime(2026, 10, 10, 4, 2, tzinfo=UTC)  # 12:02 Shanghai
    assert await scheduler.run_due(now, send) == 1
    assert await scheduler.run_due(now, send) == 0
    assert sent == [(34, "scheduled hello")]

    restarted_store = GroupSettingsStore(tmp_path / "schedule.db")
    await restarted_store.initialize()
    restarted = ProactiveScheduler(
        options, restarted_store, ProactiveSender(options, restarted_store)
    )
    assert await restarted.run_due(now, send) == 0
    assert sent == [(34, "scheduled hello")]


async def test_scheduler_skips_early_missed_quiet_and_disconnected(tmp_path):
    options, store, scheduler = await setup_scheduler(tmp_path)
    sent = []

    async def send(group_id, text):
        sent.append((group_id, text))

    assert await scheduler.run_due(datetime(2026, 10, 10, 3, 59, tzinfo=UTC), send) == 0
    assert await scheduler.run_due(datetime(2026, 10, 10, 4, 6, tzinfo=UTC), send) == 0
    assert (
        await scheduler.run_due(datetime(2026, 10, 10, 4, 2, tzinfo=UTC), send, connected=False)
        == 0
    )
    options.proactive_enabled = False
    assert await scheduler.run_due(datetime(2026, 10, 10, 4, 2, tzinfo=UTC), send) == 0
    options.proactive_enabled = True
    await store.set_proactive_enabled(34, False)
    assert await scheduler.run_due(datetime(2026, 10, 10, 4, 2, tzinfo=UTC), send) == 0
    assert sent == []


async def test_scheduler_observes_quiet_hours_and_timezone_day_claim(tmp_path):
    options, store, scheduler = await setup_scheduler(
        tmp_path,
        proactive_timezone="America/New_York",
        proactive_quiet_start="23:59",
        proactive_quiet_end="00:00",
    )
    await store.set_schedule(34, 1, 30, "DST message", owner_id=12)
    sent = []

    async def send(group_id, text):
        sent.append(text)

    # 2026-11-01 05:30 UTC is the first 01:30 in New York during the fall DST fold.
    assert await scheduler.run_due(datetime(2026, 11, 1, 5, 30, tzinfo=UTC), send) == 1
    assert await scheduler.run_due(datetime(2026, 11, 1, 6, 30, tzinfo=UTC), send) == 0
    assert sent == ["DST message"]


async def test_scheduler_claims_before_send_and_never_retries_failure(tmp_path):
    _, _, scheduler = await setup_scheduler(tmp_path)
    attempts = []

    async def fail(group_id, text):
        attempts.append(text)
        raise RuntimeError("private API response")

    now = datetime(2026, 10, 10, 4, 2, tzinfo=UTC)
    assert await scheduler.run_due(now, fail) == 0
    assert await scheduler.run_due(now, fail) == 0
    assert attempts == ["scheduled hello"]


async def test_scheduler_respects_quiet_hours_without_claiming_and_admin_schedule(tmp_path):
    options, store, scheduler = await setup_scheduler(tmp_path)
    await store.set_schedule(34, 23, 30, "quiet", owner_id=12)
    sent = []

    async def send(group_id, text):
        sent.append(text)

    # 15:32 UTC is 23:32 in Shanghai, inside the configured quiet period.
    assert await scheduler.run_due(datetime(2026, 10, 10, 15, 32, tzinfo=UTC), send) == 0
    assert sent == []
    with pytest.raises(PermissionError):
        await scheduler.set_schedule(PermissionContext(23, 34, "member"), 9, 0, "no")
    await scheduler.set_schedule(PermissionContext(12, 34, "admin"), 9, 0, "new schedule")
    assert (await scheduler.get_schedule(34)).text == "new schedule"


async def test_schedule_edit_during_tick_prevents_claiming_stale_message(tmp_path):
    _, store, _ = await setup_scheduler(tmp_path)
    previous = (await store.list_schedules())[0]
    await store.set_schedule(34, 12, 1, "edited text", owner_id=12)
    assert not await store.claim_schedule(34, "2026-10-10", expected=previous)
    assert (await store.get_schedule(34)).last_attempted_date is None


async def test_delete_recreate_and_restart_do_not_reset_daily_claim(tmp_path):
    options, store, scheduler = await setup_scheduler(tmp_path)
    sent = []

    async def send(group_id, text):
        sent.append(text)

    now = datetime(2026, 10, 10, 4, 2, tzinfo=UTC)
    assert await scheduler.run_due(now, send) == 1
    await store.delete_schedule(34)
    await store.set_schedule(34, 12, 0, "replacement", owner_id=12)
    restarted = ProactiveScheduler(options, store, ProactiveSender(options, store))
    assert await restarted.run_due(now, send) == 0
    assert sent == ["scheduled hello"]
    assert await restarted.run_due(datetime(2026, 10, 11, 4, 2, tzinfo=UTC), send) == 1


@pytest.mark.parametrize("already_sent", [False, True])
async def test_midnight_grace_preserves_occurrence_and_daily_claims(tmp_path, already_sent):
    options, store, scheduler = await setup_scheduler(
        tmp_path,
        proactive_timezone="UTC",
        proactive_quiet_start="13:00",
        proactive_quiet_end="14:00",
    )
    await store.set_schedule(34, 23, 58, "midnight", owner_id=12)
    sent = []

    async def send(group_id, text):
        sent.append(text)

    if already_sent:
        assert await scheduler.run_due(datetime(2026, 10, 10, 23, 58, tzinfo=UTC), send) == 1
    restarted = ProactiveScheduler(options, store, ProactiveSender(options, store))
    assert await restarted.run_due(datetime(2026, 10, 11, 0, 1, tzinfo=UTC), send) == (
        0 if already_sent else 1
    )
    assert sent == ["midnight"]
    restarted = ProactiveScheduler(options, store, ProactiveSender(options, store))
    assert await restarted.run_due(datetime(2026, 10, 11, 0, 2, tzinfo=UTC), send) == 0
    if not already_sent:
        await store.delete_schedule(34)
        await store.set_schedule(34, 23, 58, "replacement", owner_id=12)
        assert await restarted.run_due(datetime(2026, 10, 11, 23, 58, tzinfo=UTC), send) == 0
