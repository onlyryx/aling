from aling.services.features import FeatureService


def test_ai_tools_runtime_switch_is_owner_only_strict_and_persistent(runtime_panel):
    client, runtime = runtime_panel
    assert client.get("/api/admin/runtime-settings/ai-tools").json() == {"enabled": False}
    response = client.put("/api/admin/runtime-settings/ai-tools", json={"enabled": True})
    assert response.status_code == 200
    assert response.json() == {"enabled": True}
    assert (
        client.put("/api/admin/runtime-settings/ai-tools", json={"enabled": 1}).status_code == 422
    )
    assert (
        client.put(
            "/api/admin/runtime-settings/ai-tools", json={"enabled": False, "owner_id": 4}
        ).status_code
        == 422
    )

    async def restart():
        runtime.settings.ai_tools_enabled = False
        replacement = FeatureService(runtime.settings, runtime.store)
        await replacement.initialize()
        assert await replacement.ai_tools_enabled() is True

    client.portal.call(restart)
