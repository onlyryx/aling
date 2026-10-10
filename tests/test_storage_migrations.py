import asyncio
import sqlite3

import pytest

from aling.core.storage import GroupSettingsStore


async def test_migration_preserves_legacy_group_settings_and_is_repeatable(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE group_settings ("
            "group_id INTEGER PRIMARY KEY, proactive_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK(proactive_enabled IN (0, 1)))"
        )
        conn.executemany(
            "INSERT INTO group_settings (group_id, proactive_enabled) VALUES (?, ?)",
            [(34, 1), (56, 0)],
        )

    store = GroupSettingsStore(path)
    await store.initialize()
    await store.initialize()

    assert await store.is_proactive_enabled(34)
    assert not await store.is_proactive_enabled(56)
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT group_id, proactive_enabled FROM group_settings ORDER BY group_id"
        ).fetchall() == [(34, 1), (56, 0)]
        version = conn.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()
        assert version == (3,)


async def test_group_feature_override_cannot_exceed_global_switch(tmp_path):
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()

    assert await store.is_feature_enabled(34, "web", global_enabled=True)
    assert not await store.is_feature_enabled(34, "web", global_enabled=False)
    await store.set_feature_enabled(34, "web", True)
    assert await store.is_feature_enabled(34, "web", global_enabled=True)
    assert not await store.is_feature_enabled(34, "web", global_enabled=False)
    await store.set_feature_enabled(34, "web", False)
    assert not await store.is_feature_enabled(34, "web", global_enabled=True)


async def test_deployment_feature_switch_persists_in_separate_setting(tmp_path):
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    assert await store.get_global_feature_settings() == {}

    await store.set_global_feature_enabled("web", False)
    await store.set_global_feature_enabled("meme", True)
    assert await store.get_global_feature_settings() == {"web": False, "meme": True}


async def test_unknown_feature_and_invalid_schedule_values_are_rejected(tmp_path):
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()

    with pytest.raises(ValueError, match="feature"):
        await store.set_feature_enabled(34, "shell", True)
    for hour, minute, text in [
        (24, 0, "valid"),
        (10, 60, "valid"),
        (10, 5, "  "),
        (10, 5, "x" * 501),
        (10, 5, "valid\x00hidden"),
    ]:
        with pytest.raises(ValueError):
            await store.set_schedule(34, hour, minute, text, owner_id=12)


async def test_one_schedule_claim_succeeds_per_group_and_local_date(tmp_path):
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    await store.set_schedule(34, 9, 30, "Good morning", owner_id=12)

    claims = await asyncio.gather(
        store.claim_schedule(34, "2026-10-10"),
        store.claim_schedule(34, "2026-10-10"),
    )

    assert sorted(claims) == [False, True]
    assert await store.claim_schedule(34, "2026-10-11")


async def test_schedule_update_keeps_delivery_claim_and_delete_removes_schedule(tmp_path):
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    await store.set_schedule(34, 9, 30, "First", owner_id=12)
    assert await store.claim_schedule(34, "2026-10-10")
    await store.set_schedule(34, 10, 0, "Updated", owner_id=13)

    schedule = await store.get_schedule(34)
    assert schedule is not None
    assert (schedule.hour, schedule.minute, schedule.text, schedule.owner_id) == (
        10,
        0,
        "Updated",
        13,
    )
    assert schedule.last_attempted_date == "2026-10-10"
    assert not await store.claim_schedule(34, "2026-10-10")
    assert await store.delete_schedule(34)
    assert await store.get_schedule(34) is None
    assert not await store.delete_schedule(34)


async def test_v2_migration_keeps_claim_after_schedule_replacement(tmp_path):
    path = tmp_path / "v2.db"
    store = GroupSettingsStore(path)
    await store.initialize()
    await store.set_schedule(34, 9, 30, "Old version", owner_id=12)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE proactive_schedule_claims")
        conn.execute("UPDATE schema_version SET version=2")
        conn.execute("UPDATE proactive_schedules SET last_attempted_date='2026-10-10'")
    await store.initialize()
    await store.delete_schedule(34)
    await store.set_schedule(34, 10, 0, "Replacement", owner_id=12)
    assert not await store.claim_schedule(34, "2026-10-10")
    assert not await store.claim_schedule(34, "2026-10-09")
    assert await store.claim_schedule(34, "2026-10-11")
