import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from aling.services.status import ConnectionStatus


class FakeBot:
    def __init__(self, result=None, groups=None):
        self.result = result or {"online": False, "good": True}
        self.groups = groups or []
        self.calls = []

    async def call_api(self, action):
        self.calls.append(action)
        return self.groups if action == "get_group_list" else self.result


async def test_overview_separates_process_websocket_and_qq():
    status = ConnectionStatus()
    assert status.overview(version="test")["process_available"] is True
    assert status.overview(version="test")["onebot_connected"] is False
    bot = FakeBot()
    status.connected_to(bot)
    assert await status.refresh_qq(bot) == {
        "online": False,
        "good": True,
        "queried_at": status.overview(version="test")["qq"]["queried_at"],
    }
    await status.refresh_qq(bot)
    assert bot.calls == ["get_status"]
    status.disconnected_from(bot)
    assert status.overview(version="test")["qq"]["online"] is None


async def test_refresh_timeout_and_invalid_result_are_unknown(monkeypatch):
    status = ConnectionStatus()
    bot = FakeBot({"online": "true", "good": 1})
    assert (await status.refresh_qq(bot))["online"] is None
    assert (await status.refresh_qq(bot))["good"] is None
    status = ConnectionStatus()

    class TimeoutBot:
        async def call_api(self, action):
            raise TimeoutError("private URL and content")

    result = await status.refresh_qq(TimeoutBot())
    assert result["online"] is None
    assert "private" not in str(status.overview(version="test"))


async def test_concurrent_refresh_and_stale_cache():
    status = ConnectionStatus()
    bot = FakeBot()
    await asyncio.gather(status.refresh_qq(bot), status.refresh_qq(bot))
    assert bot.calls == ["get_status"]
    status._qq_cached_at -= 31
    assert status.overview(version="test")["qq"]["online"] is None
    await status.refresh_qq(bot)
    assert bot.calls == ["get_status", "get_status"]


def test_diagnostics_are_fixed_bounded_and_expire():
    status = ConnectionStatus()
    with pytest.raises(ValueError):
        status.record_diagnostic("https://secret", "private prompt")
    for _ in range(101):
        status.record_diagnostic("ai", "request_failed", exception_type="HTTPError")
    assert len(status.overview(version="test")["diagnostics"]) == 100
    status._diagnostics[0]["at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    assert len(status.overview(version="test")["diagnostics"]) == 99


def test_overview_safe_allowlist(runtime_panel):
    client, runtime = runtime_panel
    payload = client.get("/api/admin/overview").json()
    assert set(payload) == {
        "version",
        "started_at",
        "process_available",
        "onebot_connected",
        "last_event_at",
        "qq",
        "diagnostics",
        "model",
        "persona",
    }
    assert runtime.settings.ai_api_key.get_secret_value() not in str(payload)
    assert runtime.settings.ai_base_url not in str(payload)
    assert runtime.settings.ai_system_prompt not in str(payload)


def test_groups_read_saved_allowed_snapshot_without_implicit_qq(runtime_panel):
    client, runtime = runtime_panel
    bot = FakeBot(
        groups=[{"group_id": i, "group_name": "<script>name</script>"} for i in range(1, 1002)]
    )
    runtime.status.connected_to(bot)
    result = client.get("/api/admin/groups").json()
    assert result["items"] == [{"group_id": 34, "name": None, "allowed": True}]
    assert bot.calls == []
    result = client.post("/api/admin/groups/refresh").json()
    assert result["truncated"] is True
    assert result["total"] == 1000
    assert len(result["items"]) == 100
    assert result["items"][0]["name"] == "<script>name</script>"
    assert client.get("/api/admin/groups?limit=101").status_code == 422
    assert bot.calls == ["get_group_list"]


async def test_disconnect_during_qq_refresh_cannot_publish_online():
    status = ConnectionStatus()
    started, finish = asyncio.Event(), asyncio.Event()

    class SlowBot:
        async def call_api(self, action):
            started.set()
            await finish.wait()
            return {"online": True, "good": True}

    bot = SlowBot()
    status.connected_to(bot)
    refresh = asyncio.create_task(status.refresh_qq(bot))
    await started.wait()
    status.disconnected_from(bot)
    finish.set()
    assert (await refresh)["online"] is None
    assert status.overview(version="test")["qq"]["online"] is None


async def test_status_query_has_ten_second_total_budget(monkeypatch):
    original = asyncio.timeout
    budgets = []

    def short_timeout(seconds):
        budgets.append(seconds)
        return original(0.01)

    monkeypatch.setattr("aling.services.status.asyncio.timeout", short_timeout)

    class SlowBot:
        async def call_api(self, action):
            await asyncio.Event().wait()

    result = await ConnectionStatus().refresh_qq(SlowBot())
    assert result["online"] is None and budgets == [10]


async def test_group_refresh_deadline_includes_waiting_for_lock(monkeypatch):
    original_timeout = asyncio.timeout
    budgets = []

    def short_timeout(seconds):
        budgets.append(seconds)
        return original_timeout(0.03)

    monkeypatch.setattr("aling.services.status.asyncio.timeout", short_timeout)
    status = ConnectionStatus()
    status.group_snapshot = {123: "existing"}
    status.groups_queried_at = "2026-10-12T00:00:00+00:00"
    status.groups_truncated = True
    previous_snapshot = dict(status.group_snapshot)
    previous_queried_at = status.groups_queried_at
    previous_truncated = status.groups_truncated
    bot = FakeBot()
    await status._groups_lock.acquire()
    refresh = asyncio.create_task(status.refresh_groups(bot))
    try:
        done, _ = await asyncio.wait({refresh}, timeout=0.045)
        assert refresh in done, "group refresh must include lock wait in its total deadline"
        assert refresh.result() is False
        assert bot.calls == []
        assert status.group_snapshot == previous_snapshot
        assert status.groups_queried_at == previous_queried_at
        assert status.groups_truncated is previous_truncated
    finally:
        status._groups_lock.release()
        await asyncio.gather(refresh)
    assert budgets == [10]


def test_ai_and_web_diagnostics_never_retain_provider_content(runtime_panel):
    client, runtime = runtime_panel

    async def failing_chat(*args):
        raise ValueError("secret prompt URL credential")

    async def failing_parse(*args):
        raise ValueError("secret webpage URL credential")

    runtime.chat.client.chat = failing_chat
    runtime.web_read.client.parse = failing_parse
    client.portal.call(runtime.chat.reply, 34, "fictional question")

    async def read():
        with pytest.raises(ValueError):
            await runtime.web_read.read("https://fictional.invalid/", group_id=34)

    client.portal.call(read)
    diagnostics = client.get("/api/admin/overview").json()["diagnostics"]
    assert {(item["component"], item["code"]) for item in diagnostics} == {
        ("ai", "request_failed"),
        ("web", "read_failed"),
    }
    assert "secret" not in str(diagnostics)


async def test_replacement_qq_query_deadline_includes_waiting_for_lock(monkeypatch):
    original_timeout = asyncio.timeout
    budgets = []

    def short_timeout(seconds):
        budgets.append(seconds)
        return original_timeout(0.03)

    monkeypatch.setattr("aling.services.status.asyncio.timeout", short_timeout)
    first_started = asyncio.Event()

    class SlowBot:
        async def call_api(self, action):
            first_started.set()
            await asyncio.Event().wait()

    status = ConnectionStatus()
    original, replacement = SlowBot(), SlowBot()
    status.connected_to(original)
    first = asyncio.create_task(status.refresh_qq(original))
    await first_started.wait()
    status.connected_to(replacement)
    second = asyncio.create_task(status.refresh_qq(replacement))
    try:
        done, _ = await asyncio.wait({second}, timeout=0.045)
        assert second in done, "replacement query must include lock wait in its total deadline"
        assert second.result()["online"] is None
        assert second.result()["good"] is None
    finally:
        await asyncio.gather(first, second)
    assert budgets == [10, 10]
