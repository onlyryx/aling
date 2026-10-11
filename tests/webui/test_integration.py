"""The optional panel and the existing bot share one application safely."""


def test_panel_routes_share_runtime_and_keep_health_when_disabled(
    caplog, panel_factory, runtime_panel
):
    client, runtime = runtime_panel

    assert client.get("/webui/").status_code == 200
    assert client.get("/api/admin/overview").json()["model"] == {
        "configured": True,
        "name": "fictional-model",
    }
    assert client.get("/api/admin/groups").json()["items"][0]["group_id"] == 34
    assert client.get("/api/admin/features/global").status_code == 200
    assert client.get("/api/admin/memes").status_code == 200
    assert client.get("/api/admin/schedules").status_code == 200
    assert runtime.status.overview(version="test")["diagnostics"] == []
    disabled = panel_factory(enabled=False)
    assert disabled.get("/webui/").status_code == 404
    assert disabled.get("/api/admin/overview").status_code == 404
    assert disabled.get("/health").json() == {"status": "ok"}
    assert disabled.get("/onebot/v11/internal-probe").json() == {"preserved": True}

    response = client.get("/api/admin/overview")
    for private_value in (
        "fictional-panel-key",
        "fictional-model-key",
        "fictional-onebot-key",
        "fictional private persona instruction",
    ):
        assert private_value not in response.text
        assert private_value not in caplog.text
