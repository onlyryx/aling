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
        assert version == (4,)


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


async def test_v4_upgrade_preserves_v3_rows_and_safe_policy_defaults(tmp_path):
    path = tmp_path / "v3.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE schema_version(singleton INTEGER PRIMARY KEY, version INTEGER);
            INSERT INTO schema_version VALUES(1, 3);
            CREATE TABLE group_settings(group_id INTEGER PRIMARY KEY, proactive_enabled INTEGER);
            INSERT INTO group_settings VALUES(34, 1);
            CREATE TABLE group_feature_settings(
                group_id INTEGER, feature TEXT CHECK(feature IN ('ai','web','meme','memory')),
                enabled INTEGER, PRIMARY KEY(group_id, feature));
            INSERT INTO group_feature_settings VALUES(34, 'ai', 1), (34, 'web', 0);
            CREATE TABLE deployment_feature_settings(
                feature TEXT PRIMARY KEY CHECK(feature IN ('ai','web','meme','memory')),
                enabled INTEGER);
            INSERT INTO deployment_feature_settings VALUES('ai', 1), ('meme', 1);
            CREATE TABLE proactive_schedules(
                group_id INTEGER PRIMARY KEY, hour INTEGER, minute INTEGER, message TEXT,
                owner_id INTEGER, last_attempted_date TEXT);
            INSERT INTO proactive_schedules VALUES(34, 9, 30, 'preserve me', 12, '2026-10-11');
            CREATE TABLE proactive_schedule_claims(
                group_id INTEGER PRIMARY KEY, last_attempted_date TEXT, last_occurrence_date TEXT);
            INSERT INTO proactive_schedule_claims VALUES(34, '2026-10-11', '2026-10-10');
            CREATE TABLE local_notes(note TEXT);
            INSERT INTO local_notes VALUES('arbitrary local data');
        """)
        before = {
            name: conn.execute(f"SELECT * FROM {name}").fetchall()
            for name in (
                "group_settings",
                "group_feature_settings",
                "deployment_feature_settings",
                "proactive_schedules",
                "proactive_schedule_claims",
                "local_notes",
            )
        }
    store = GroupSettingsStore(path)
    await store.initialize()
    await store.initialize()
    with sqlite3.connect(path) as conn:
        for name, rows in before.items():
            assert conn.execute(f"SELECT * FROM {name}").fetchall() == rows
        assert conn.execute("SELECT version FROM schema_version").fetchone() == (4,)
    for feature in ("search", "online_meme"):
        assert not await store.is_feature_enabled(34, feature, global_enabled=True)
    deployment = await store.get_deployment_search_policy()
    assert deployment.allowed_purposes == frozenset({"technical", "ordinary_meme"})
    assert deployment.sources == ()
    assert deployment.revision == 0
    group = await store.get_group_search_policy(34)
    assert group.meme_mode == "local"
    assert group.revision == 0
    assert not await store.get_search_consent()
    await store.set_global_feature_enabled("search", True)
    await store.set_feature_enabled(34, "online_meme", True)


async def test_ai_tools_seed_once_and_owner_choice_survives_restart(tmp_path):
    store = GroupSettingsStore(tmp_path / "settings.db", superusers={12})
    await store.initialize()
    assert await store.get_ai_tools_enabled(True)
    assert await store.get_ai_tools_enabled(False)
    await store.set_ai_tools_enabled(12, False)
    await store.initialize()
    assert not await store.get_ai_tools_enabled(True)
    with pytest.raises(PermissionError):
        await store.set_ai_tools_enabled(13, True)
    with pytest.raises(PermissionError):
        await store.set_search_consent(13, True)
    await store.set_search_consent(12, True)
    assert await store.get_search_consent()
    await store.set_search_consent(12, False)
    assert not await store.get_search_consent()
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT ai_tools_seeded FROM deployment_search_policy").fetchone() == (
            1,
        )


async def test_policy_roundtrip_revisions_and_group_narrowing(tmp_path):
    from dataclasses import replace

    from aling.core.storage import (
        DeploymentSearchPolicy,
        GroupSearchPolicy,
        SearchLimits,
        SearchSourceRule,
    )

    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    source = SearchSourceRule("python", "technical", "DOCS.PYTHON.ORG.", path_prefix="/3")
    policy = DeploymentSearchPolicy(sources=(source,))
    assert await store.set_deployment_search_policy(policy) == 1
    stored = await store.get_deployment_search_policy()
    assert stored.sources[0].host == "docs.python.org"
    assert stored.revision == 1
    group = GroupSearchPolicy(
        allowed_purposes=frozenset({"technical"}),
        source_ids=frozenset({"python"}),
        limits=SearchLimits(group_hourly_limit=10),
        meme_mode="auto",
    )
    assert await store.set_group_search_policy(34, group) == 1
    assert await store.set_group_search_policy(34, group) == 2
    await store.initialize()
    saved = await store.get_group_search_policy(34)
    assert saved.allowed_purposes == frozenset({"technical"})
    assert saved.source_ids == frozenset({"python"})
    assert saved.limits.group_hourly_limit == 10
    assert saved.meme_mode == "auto"
    assert saved.revision == 2
    assert 34 in await store.list_configured_group_ids()
    for widened in (
        replace(group, allowed_purposes=frozenset({"gaming"})),
        replace(group, source_ids=frozenset({"unapproved"})),
        replace(group, limits=SearchLimits(group_hourly_limit=31)),
        replace(group, limits=SearchLimits(user_cooldown_seconds=14)),
    ):
        with pytest.raises(ValueError):
            await store.set_group_search_policy(34, widened)
    assert (await store.get_group_search_policy(34)).revision == 2
    assert await store.set_deployment_search_policy(stored) == 2


async def test_policy_rejects_unstructured_and_unsafe_values_before_sql(tmp_path):
    from dataclasses import replace

    from aling.core.storage import DeploymentSearchPolicy, SearchLimits, SearchSourceRule

    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    policy = DeploymentSearchPolicy()
    invalid = [
        replace(policy, allowed_purposes=frozenset({"unknown"})),
        replace(policy, allowed_purposes="technical"),
        replace(policy, limits=SearchLimits(max_results=4)),
        replace(policy, limits=SearchLimits(group_hourly_limit=True)),
        replace(policy, sources=(SearchSourceRule("bad", "technical", "example.org:443"),)),
        replace(policy, sources=(SearchSourceRule("bad", "technical", "*.example.org"),)),
        replace(
            policy,
            sources=(SearchSourceRule("bad", "technical", "example.org", path_prefix="/../x"),),
        ),
        replace(
            policy,
            sources=(SearchSourceRule("bad", "technical", "example.org", path_prefix="/%2e%2e/x"),),
        ),
        replace(policy, sources=(SearchSourceRule("bad", "unknown", "example.org"),)),
        replace(
            policy, sources=(SearchSourceRule("bad", "technical", "example.org", kind="shell"),)
        ),
    ]
    for value in invalid:
        with pytest.raises(ValueError):
            await store.set_deployment_search_policy(value)
    assert (await store.get_deployment_search_policy()).revision == 0


async def test_failed_migration_rolls_back_both_feature_tables_and_version(tmp_path):
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    await store.set_feature_enabled(34, "web", True)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE schema_version SET version=3")
        conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute("INSERT INTO deployment_feature_settings VALUES('web', 2)")
        original_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='group_feature_settings'"
        ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        await store.initialize()
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone() == (3,)
        assert conn.execute("SELECT * FROM group_feature_settings").fetchall() == [(34, "web", 1)]
        assert conn.execute("SELECT * FROM deployment_feature_settings").fetchall() == [("web", 2)]
        assert (
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='group_feature_settings'"
            ).fetchone()[0]
            == original_sql
        )
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%_v4'").fetchall() == []
        )


async def test_concurrent_policy_mutations_keep_monotonic_revisions(tmp_path):
    from aling.core.storage import DeploymentSearchPolicy, GroupSearchPolicy

    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    assert sorted(
        await asyncio.gather(
            *(store.set_deployment_search_policy(DeploymentSearchPolicy()) for _ in range(4))
        )
    ) == [1, 2, 3, 4]
    assert sorted(
        await asyncio.gather(
            *(store.set_group_search_policy(34, GroupSearchPolicy()) for _ in range(4))
        )
    ) == [1, 2, 3, 4]


async def test_structured_policy_rejects_wrong_field_types_with_value_error(tmp_path):
    from dataclasses import replace

    from aling.core.storage import DeploymentSearchPolicy, GroupSearchPolicy, SearchSourceRule

    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    for source in (
        SearchSourceRule("bad", [], "example.org"),
        SearchSourceRule("bad", "technical", "example.org", kind=[]),
    ):
        with pytest.raises(ValueError):
            await store.set_deployment_search_policy(DeploymentSearchPolicy(sources=(source,)))
    with pytest.raises(ValueError):
        await store.set_group_search_policy(34, replace(GroupSearchPolicy(), meme_mode=[]))
