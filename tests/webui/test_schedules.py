from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


@pytest.fixture
def schedule_runtime(runtime_panel):
    client, runtime = runtime_panel
    runtime.settings.proactive_enabled = True
    recorder = []
    clock = datetime(2026, 10, 10, 4, 2, tzinfo=UTC)  # 12:02 Asia/Shanghai

    async def send(group_id, text):
        recorder.append((group_id, text))

    async def claim_today(group_id: int) -> None:
        await runtime.store.set_proactive_enabled(group_id, True)
        schedule = await runtime.store.get_schedule(group_id)
        if schedule is None:
            await runtime.store.set_schedule(
                group_id, 12, 0, "fictional scheduled text", owner_id=12345
            )
            schedule = await runtime.store.get_schedule(group_id)
        assert schedule is not None
        assert await runtime.store.claim_schedule(
            group_id, "2026-10-10", expected=schedule, occurrence_date="2026-10-10"
        )

    async def run_today_again() -> int:
        return await runtime.scheduler.run_due(clock, send, connected=True)

    return SimpleNamespace(
        client=client,
        runtime=runtime,
        recorder=recorder,
        claim_today=lambda group_id: client.portal.call(claim_today, group_id),
        run_today_again=lambda: client.portal.call(run_today_again),
    )


@pytest.mark.parametrize("hour,minute", [(0, 0), (23, 59)])
def test_schedule_accepts_boundary_times(schedule_runtime, hour, minute):
    response = schedule_runtime.client.put(
        "/api/admin/groups/34/schedule",
        json={"hour": hour, "minute": minute, "text": "x"},
    )
    assert response.status_code == 200
    assert response.json()["schedule"]["hour"] == hour
    assert response.json()["schedule"]["minute"] == minute


def test_schedule_rejects_invalid_time_and_text_lengths(schedule_runtime):
    client = schedule_runtime.client
    for body in [
        {"hour": 24, "minute": 0, "text": "x"},
        {"hour": 12, "minute": 0, "text": ""},
        {"hour": 12, "minute": 0, "text": "x" * 501},
    ]:
        assert client.put("/api/admin/groups/34/schedule", json=body).status_code == 422
    assert (
        client.put(
            "/api/admin/groups/34/schedule",
            json={"hour": 12, "minute": 0, "text": "x" * 500},
        ).status_code
        == 200
    )


def test_schedule_rejects_spoofed_identity_and_disallowed_group(schedule_runtime):
    client = schedule_runtime.client
    assert (
        client.put(
            "/api/admin/groups/34/schedule",
            json={"hour": 12, "minute": 0, "text": "safe", "owner_id": 999},
        ).status_code
        == 422
    )
    denied = client.put(
        "/api/admin/groups/56/schedule",
        json={"hour": 12, "minute": 0, "text": "safe"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "scope_restricted"
    proactive = client.put("/api/admin/groups/34/proactive", json={"enabled": True})
    assert proactive.status_code == 200
    assert proactive.json()["proactive_enabled"] is True
    assert client.put("/api/admin/groups/56/proactive", json={"enabled": True}).status_code == 403
    assert (
        client.put(
            "/api/admin/groups/34/proactive", json={"enabled": True, "owner_id": 999}
        ).status_code
        == 422
    )


def test_global_proactive_disable_keeps_saved_schedule_inactive(schedule_runtime):
    client, runtime = schedule_runtime.client, schedule_runtime.runtime
    runtime.settings.proactive_enabled = False
    response = client.put(
        "/api/admin/groups/34/schedule",
        json={"hour": 12, "minute": 0, "text": "fictional scheduled text"},
    )
    assert response.status_code == 200
    item = next(
        item
        for item in client.get("/api/admin/schedules").json()["items"]
        if item["group_id"] == 34
    )
    assert item["schedule"]["text"] == "fictional scheduled text"
    assert item["active"] is False
    assert "global_off" in item["inactive_reasons"]
    assert schedule_runtime.run_today_again() == 0
    assert schedule_runtime.recorder == []


def test_panel_recreation_does_not_reset_daily_claim(schedule_runtime):
    client = schedule_runtime.client
    schedule_runtime.claim_today(34)
    assert client.delete("/api/admin/groups/34/schedule").status_code == 200
    assert (
        client.put(
            "/api/admin/groups/34/schedule",
            json={"hour": 12, "minute": 0, "text": "fictional scheduled text"},
        ).status_code
        == 200
    )
    assert schedule_runtime.run_today_again() == 0
    assert schedule_runtime.recorder == []


def test_schedule_page_module_is_served(schedule_runtime):
    response = schedule_runtime.client.get("/webui/pages/schedules.js")
    assert response.status_code == 200
    assert "inactive_reasons" in response.text
    assert "last_attempted_date" in response.text


def test_schedule_page_save_state_survives_failures_and_group_toggle():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the schedule page event handlers")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            node,
            str(root / "tests/webui/fixtures/schedules_save_state.mjs"),
            str(root / "src/aling/webui/static/pages/schedules.js"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
