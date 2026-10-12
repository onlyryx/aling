from aling.core.permissions import PermissionContext
from aling.services.features import FeatureService


def test_group_toggle_effective_and_actor_spoof_rejected(runtime_panel):
    client, _ = runtime_panel
    assert client.put("/api/admin/features/global/web", json={"enabled": True}).status_code == 200
    result = client.put("/api/admin/groups/34/features/web", json={"enabled": True})
    assert result.json()["effective"] is True
    assert result.json()["group_requested"] is True
    assert (
        client.put(
            "/api/admin/groups/34/features/web",
            json={"enabled": True, "user_id": 99, "role": "owner"},
        ).status_code
        == 422
    )
    assert (
        client.put("/api/admin/groups/56/features/web", json={"enabled": True}).json()["detail"]
        == "scope_restricted"
    )
    assert client.put("/api/admin/groups/34/features/web", json={"enabled": 1}).status_code == 422
    assert (
        client.put("/api/admin/features/global/unknown", json={"enabled": False}).status_code == 404
    )


def test_disabled_dependency_and_global_lists(runtime_panel):
    client, _ = runtime_panel
    result = client.put("/api/admin/groups/34/features/web", json={"enabled": True})
    assert result.status_code == 409
    assert result.json()["detail"] == "feature_unavailable"
    assert set(client.get("/api/admin/features/global").json()) == {
        "ai",
        "web",
        "meme",
        "memory",
        "search",
        "online_meme",
    }
    assert client.get("/api/admin/features/global/web").json()["blocked_by"] == ["global_off"]
    assert (
        "scope_restricted"
        in client.get("/api/admin/groups/56/features").json()["web"]["blocked_by"]
    )


def test_memory_requires_session_bound_confirmation_and_ai_recheck(runtime_panel):
    client, _ = runtime_panel
    for feature in ["ai", "memory"]:
        assert (
            client.put(f"/api/admin/features/global/{feature}", json={"enabled": True}).status_code
            == 200
        )
    client.put("/api/admin/groups/34/features/ai", json={"enabled": True})
    assert (
        client.put("/api/admin/groups/34/features/memory", json={"enabled": True}).json()["detail"]
        == "memory_confirmation_required"
    )
    consent = client.post("/api/admin/groups/34/memory-confirmation").json()
    assert consent["expires_in"] == 60 and "不写入日志或数据库" in consent["notice"]
    assert (
        client.post(
            "/api/admin/groups/34/memory-confirmation/confirm",
            json={"ticket": consent["ticket"], "scope": "qq"},
        ).status_code
        == 422
    )
    client.put("/api/admin/groups/34/features/ai", json={"enabled": False})
    assert (
        client.post(
            "/api/admin/groups/34/memory-confirmation/confirm", json={"ticket": consent["ticket"]}
        ).status_code
        == 409
    )
    client.put("/api/admin/groups/34/features/ai", json={"enabled": True})
    consent = client.post("/api/admin/groups/34/memory-confirmation").json()
    result = client.post(
        "/api/admin/groups/34/memory-confirmation/confirm", json={"ticket": consent["ticket"]}
    )
    assert result.json()["effective"] is True
    assert (
        client.post(
            "/api/admin/groups/34/memory-confirmation/confirm", json={"ticket": consent["ticket"]}
        ).status_code
        == 409
    )


def test_api_and_qq_service_share_persistent_state(runtime_panel):
    client, runtime = runtime_panel
    client.put("/api/admin/features/global/web", json={"enabled": True})
    client.put("/api/admin/groups/34/features/web", json={"enabled": True})

    async def verify():
        restarted = FeatureService(runtime.settings, runtime.store)
        await restarted.initialize()
        assert (await restarted.state(34, "web")).effective is True
        await runtime.features.set(PermissionContext(12345, 34, "member"), "web", False)
        assert await runtime.store.get_group_feature_settings(34) == {"web": False}
        await runtime.store.set_proactive_enabled(78, False)
        assert await runtime.store.list_configured_group_ids() == [34, 78]

    client.portal.call(verify)
    assert client.get("/api/admin/groups/34/features").json()["web"]["group_requested"] is False
    assert client.get("/api/admin/groups").json()["total"] == 2


def test_memory_ticket_cannot_cross_login_sessions(runtime_panel):
    from tests.webui.conftest import TOKEN

    client, _ = runtime_panel
    for feature in ["ai", "memory"]:
        client.put(f"/api/admin/features/global/{feature}", json={"enabled": True})
    ticket = client.post("/api/admin/groups/34/memory-confirmation").json()["ticket"]
    session = client.post("/api/admin/session", json={"token": TOKEN}).json()
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    assert (
        client.post(
            "/api/admin/groups/34/memory-confirmation/confirm", json={"ticket": ticket}
        ).status_code
        == 409
    )


def test_safe_save_failure_and_group_id_validation(runtime_panel, monkeypatch):
    import sqlite3

    client, runtime = runtime_panel

    async def failed_save(*args):
        raise sqlite3.OperationalError("private credential and DB path")

    monkeypatch.setattr(runtime.store, "set_global_feature_enabled", failed_save)
    response = client.put("/api/admin/features/global/web", json={"enabled": True})
    assert response.status_code == 503 and response.json()["detail"] == "storage_unavailable"
    assert "private" not in response.text
    assert client.get("/api/admin/features/global/web").json()["global_requested"] is False
    assert client.put("/api/admin/groups/0/features/web", json={"enabled": True}).status_code == 422
    assert (
        client.put(
            "/api/admin/groups/9223372036854775808/features/web", json={"enabled": True}
        ).status_code
        == 422
    )


def test_page_modules_are_served_inside_panel(runtime_panel):
    client, _ = runtime_panel
    for path in ["overview", "features", "search"]:
        response = client.get(f"/webui/pages/{path}.js")
        assert response.status_code == 200
        assert "adminFetch" in response.text


def test_delayed_group_save_preserves_new_group_draft():
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the real feature page event handlers")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            node,
            str(root / "tests/webui/fixtures/features_save_switch.mjs"),
            str(root / "src/aling/webui/static/pages/features.js"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "destination form unchanged" in result.stdout
