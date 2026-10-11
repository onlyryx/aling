import json

import pytest
from pydantic import ValidationError

from aling.core.config import Settings

from .conftest import ORIGIN, TOKEN


def test_panel_disabled_keeps_health_and_no_management_routes(panel_factory):
    client = panel_factory(enabled=False)
    assert client.get("/webui/").status_code == 404
    assert client.get("/api/admin/session").status_code == 404
    assert client.get("/health").json() == {"status": "ok"}


def test_unauthenticated_read_is_denied(panel_factory):
    assert panel_factory().get("/api/admin/session").status_code == 401


def test_login_cookie_and_trusted_session_identity(panel_factory):
    client = panel_factory()
    assert (
        client.post(
            "/api/admin/session", json={"token": "wrong"}, headers={"Origin": ORIGIN}
        ).status_code
        == 401
    )
    response = client.post("/api/admin/session", json={"token": TOKEN}, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/api/admin" in cookie and "Domain=" not in cookie
    assert "Secure" not in cookie
    assert TOKEN not in response.text
    assert response.json()["owner_id"] == 12345
    assert client.get("/api/admin/session").json() == response.json()


def test_https_origin_sets_secure_cookie(panel_factory):
    origin = "https://panel.example.invalid"
    client = panel_factory(origin=origin)
    response = client.post("/api/admin/session", json={"token": TOKEN}, headers={"Origin": origin})
    assert "Secure" in response.headers["set-cookie"]


def test_logout_revokes_session(panel_client):
    session_id = panel_client.cookies.get("aling_panel_session")
    assert panel_client.delete("/api/admin/session").status_code == 204
    panel_client.cookies.set("aling_panel_session", session_id)
    assert panel_client.get("/api/admin/session").status_code == 401


def test_server_sessions_expire_evict_and_hide_secrets(panel_factory):
    from aling.webui.auth import PanelAuth

    now = [0.0]
    auth = PanelAuth(panel_factory().app.state.panel_runtime.settings, clock=lambda: now[0])
    sessions = [auth.login(TOKEN, "127.0.0.1") for _ in range(9)]
    with pytest.raises(PermissionError):
        auth.authenticate(sessions[0].session_id)
    session = auth.authenticate(sessions[-1].session_id)
    assert session.owner_id == 12345
    assert session.session_id not in repr(session)
    assert session.csrf_token not in repr(session)
    assert TOKEN not in repr(auth)
    now[0] = 8 * 3600
    with pytest.raises(PermissionError):
        auth.authenticate(session.session_id)


def test_login_failures_are_bounded_per_connection_source_and_global(panel_factory):
    from aling.webui.auth import LoginRateLimited, PanelAuth

    now = [0.0]
    auth = PanelAuth(panel_factory().app.state.panel_runtime.settings, clock=lambda: now[0])
    for _ in range(5):
        with pytest.raises(PermissionError):
            auth.login("wrong", "ip-one")
    with pytest.raises(LoginRateLimited):
        auth.login(TOKEN, "ip-one")
    for n in range(15):
        with pytest.raises(PermissionError):
            auth.login("wrong", f"ip-{n}")
    with pytest.raises(LoginRateLimited):
        auth.login(TOKEN, "new-ip")
    now[0] = 60
    assert auth.login(TOKEN, "ip-one").owner_id == 12345


@pytest.mark.parametrize(
    "override",
    [
        {},
        {"webui_admin_token": "short", "webui_owner_id": 12},
        {"webui_admin_token": "!" * 43, "webui_owner_id": 12},
        {"webui_admin_token": TOKEN, "webui_owner_id": 13},
        {"webui_admin_token": TOKEN, "webui_owner_id": 12, "webui_origin": "http://remote.invalid"},
        {
            "webui_admin_token": TOKEN,
            "webui_owner_id": 12,
            "webui_origin": "https://panel.invalid/path",
        },
    ],
)
def test_enabled_panel_requires_safe_settings(override):
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            onebot_access_token="fake",
            superusers={12},
            webui_enabled=True,
            **override,
        )
    assert TOKEN not in str(exc.value)


def test_disabled_panel_does_not_require_credentials():
    settings = Settings(_env_file=None, onebot_access_token="fake")
    assert settings.webui_enabled is False


def test_malformed_login_cannot_echo_sensitive_input(panel_factory):
    client = panel_factory()
    response = client.post(
        "/api/admin/session", json={"token": TOKEN, "owner_id": 12345}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 401
    assert TOKEN not in response.text


def test_static_shell_is_public_hardened_and_packaged(panel_factory):
    client = panel_factory()
    for path in ("/webui/", "/webui/styles.css", "/webui/app.js", "/webui/api.js"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "阿零面板登录密钥" in client.get("/webui/").text


@pytest.mark.parametrize(
    "body", [b'{"token":', b"[]", b"null", b'{"token":123}', b'{"token":{"secret":"sensitive"}}']
)
def test_malformed_login_is_safe_and_counts_as_failure(panel_factory, body):
    client = panel_factory()
    for _ in range(5):
        response = client.post("/api/admin/session", content=body, headers={"Origin": ORIGIN})
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid login credentials"}
    assert (
        client.post(
            "/api/admin/session", json={"token": TOKEN}, headers={"Origin": ORIGIN}
        ).status_code
        == 429
    )


def test_admin_validation_errors_hide_input_without_changing_public_validation(panel_client):
    response = panel_client.get("/api/admin/validation", params={"value": TOKEN})
    assert response.status_code == 422
    assert TOKEN not in response.text
    assert (
        panel_client.get("/validation", params={"value": "not-an-int"}).json()["detail"][0]["input"]
        == "not-an-int"
    )


def test_unicode_incorrect_key_is_denied_safely(panel_factory):
    response = panel_factory().post(
        "/api/admin/session", json={"token": "密钥"}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 401


def test_expiry_is_not_extended_by_reading_session(panel_factory):
    from aling.webui.auth import PanelAuth

    now = [0.0]
    auth = PanelAuth(panel_factory().app.state.panel_runtime.settings, clock=lambda: now[0])
    session = auth.login(TOKEN, "127.0.0.1")
    now[0] = 28799
    assert auth.authenticate(session.session_id) == session
    now[0] = 28800
    with pytest.raises(PermissionError):
        auth.authenticate(session.session_id)


@pytest.mark.parametrize("token", ["\ud800", "\udfff"])
def test_escaped_surrogates_in_login_are_denied_safely(panel_factory, token):
    response = panel_factory().post(
        "/api/admin/session",
        content=json.dumps({"token": token}).encode(),
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid login credentials"}


@pytest.mark.parametrize(
    "origin",
    [
        "https://white space.invalid",
        "https://panel.invalid:0",
        "https://panel.invalid/#x",
        "https://panel.invalid?x=1",
    ],
)
def test_enabled_panel_rejects_malformed_origins(origin):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            onebot_access_token="fake",
            superusers={12},
            webui_enabled=True,
            webui_admin_token=TOKEN,
            webui_owner_id=12,
            webui_origin=origin,
        )


def test_preserves_existing_sync_non_admin_validation_handler(panel_factory):
    from starlette.responses import JSONResponse

    def original_handler(request, exc):
        return JSONResponse({"detail": "Existing public validation handler"}, status_code=418)

    client = panel_factory(validation_handler=original_handler)
    response = client.get("/validation", params={"value": "not-an-int"})
    assert response.status_code == 418
    assert response.json() == {"detail": "Existing public validation handler"}
