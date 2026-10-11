from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from aling.core.config import Settings

TOKEN = "fictional-panel-key-" + "a" * 43
ORIGIN = "http://127.0.0.1:8080"


@pytest.fixture
def panel_factory(tmp_path):
    def create(
        *, enabled=True, origin=ORIGIN, client_ip="127.0.0.1", validation_handler=None, token=TOKEN
    ):
        from aling.webui import mount_webui

        settings = Settings(
            _env_file=None,
            onebot_access_token="fictional-onebot-key",
            database_path=tmp_path / "fictional.db",
            meme_dir=tmp_path / "memes",
            superusers={12345},
            webui_enabled=enabled,
            webui_admin_token=token,
            webui_owner_id=12345,
            webui_origin=origin,
        )
        app = FastAPI()
        if validation_handler is not None:
            app.add_exception_handler(RequestValidationError, validation_handler)

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.put("/api/admin/features/global/web")
        @app.post("/api/admin/memes/upload")
        async def business(request: Request):
            if request.url.path == "/api/admin/memes/upload":
                app.state.upload_started = True
            return {
                "bytes": len(await request.body()),
                "owner_id": request.state.panel_session.owner_id,
            }

        @app.get("/onebot/v11/internal-probe")
        async def onebot_probe():
            return {"preserved": True}

        @app.get("/api/admin/validation")
        @app.get("/validation")
        async def validation(value: int):
            return {"value": value}

        # No business test routes in the disabled panel fixture.
        if not enabled:
            app.router.routes = [
                r for r in app.router.routes if not r.path.startswith("/api/admin")
            ]
        mount_webui(app, SimpleNamespace(settings=settings))
        client = TestClient(app, base_url=origin, client=(client_ip, 1234))
        return client

    return create


@pytest.fixture
def panel_client(panel_factory):
    client = panel_factory()
    response = client.post("/api/admin/session", json={"token": TOKEN}, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
    return client
