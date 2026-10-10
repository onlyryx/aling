from aling.services.sessions import SessionStore


async def test_sessions_are_group_user_scoped_and_forget_only_target(tmp_path):
    sessions = SessionStore()
    await sessions.add(1, 11, "group one", "reply one")
    await sessions.add(2, 11, "group two", "reply two")
    await sessions.add(1, 12, "user two", "other reply")

    assert await sessions.get(1, 11) == [
        {"role": "user", "content": "group one"},
        {"role": "assistant", "content": "reply one"},
    ]
    assert await sessions.forget(1, 11)
    assert await sessions.get(1, 11) == []
    assert (await sessions.get(2, 11))[0]["content"] == "group two"


async def test_sessions_expire_by_ttl_and_restart_empty():
    now = [0.0]
    sessions = SessionStore(ttl_seconds=600, clock=lambda: now[0])
    await sessions.add(1, 11, "question", "answer")
    now[0] = 599
    assert await sessions.get(1, 11)
    now[0] = 1200
    assert await sessions.get(1, 11) == []
    await sessions.add(1, 11, "question", "answer")
    assert await SessionStore(clock=lambda: now[0]).get(1, 11) == []


async def test_sessions_bound_turns_characters_and_total_sessions():
    now = [0.0]
    sessions = SessionStore(
        ttl_seconds=600,
        max_turns=2,
        max_chars=16,
        max_sessions=2,
        clock=lambda: now[0],
    )
    await sessions.add(1, 11, "a", "A")
    now[0] += 1
    await sessions.add(1, 11, "b", "B")
    await sessions.add(1, 11, "c", "C")
    history = await sessions.get(1, 11)
    assert [item["content"] for item in history] == ["b", "B", "c", "C"]
    await sessions.add(1, 11, "0123456789", "abcdefghij")
    bounded = await sessions.get(1, 11)
    assert sum(len(item["content"]) for item in bounded) <= 16
    await sessions.add(2, 11, "x", "X")
    now[0] += 1
    await sessions.add(3, 11, "y", "Y")
    assert await sessions.get(1, 11) == []
    assert await sessions.get(2, 11)
    assert await sessions.get(3, 11)


async def test_clear_group_removes_only_group_sessions():
    sessions = SessionStore()
    await sessions.add(1, 11, "a", "A")
    await sessions.add(1, 12, "b", "B")
    await sessions.add(2, 11, "c", "C")

    await sessions.clear_group(1)

    assert await sessions.get(1, 11) == []
    assert await sessions.get(1, 12) == []
    assert await sessions.get(2, 11)


async def test_clearing_blocks_in_flight_session_writes():
    sessions = SessionStore()
    for clear in (
        lambda: sessions.forget(1, 11),
        lambda: sessions.clear_group(1),
        sessions.clear,
    ):
        await sessions.add(1, 11, "old question", "old answer")
        revision = sessions.revision
        await clear()
        await sessions.add(1, 11, "in flight", "late answer", expected_revision=revision)
        assert await sessions.get(1, 11) == []


async def test_idle_sessions_are_purged_without_a_new_question():
    now = [0.0]
    sessions = SessionStore(ttl_seconds=600, clock=lambda: now[0])
    await sessions.add(1, 11, "private question", "reply")
    now[0] = 600
    await sessions.expire()
    assert not sessions._sessions
