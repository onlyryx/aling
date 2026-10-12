"""Optional panel, sharing the bot's FastAPI app and Runtime."""

from importlib.resources import files
from inspect import isawaitable

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from aling.runtime import Runtime
from aling.webui.auth import PanelAuth
from aling.webui.boundary import AdminBoundaryMiddleware, is_admin_path
from aling.webui.routes_features import router as features_router
from aling.webui.routes_memes import UploadAdmission
from aling.webui.routes_memes import router as memes_router
from aling.webui.routes_schedules import router as schedules_router
from aling.webui.routes_search import router as search_router
from aling.webui.routes_session import router
from aling.webui.routes_status import router as status_router


def mount_webui(app: FastAPI, runtime: Runtime) -> None:
    if not runtime.settings.webui_enabled:
        return
    app.state.panel_runtime = runtime
    app.state.panel_auth = PanelAuth(runtime.settings)
    app.state.panel_uploads = UploadAdmission()
    app.add_middleware(
        AdminBoundaryMiddleware,
        auth=app.state.panel_auth,
        origin=runtime.settings.webui_origin,
    )
    existing_validation = app.exception_handlers.get(
        RequestValidationError, request_validation_exception_handler
    )

    @app.exception_handler(RequestValidationError)
    async def safe_admin_validation(request: Request, exc: RequestValidationError):
        if is_admin_path(request.url.path):
            return JSONResponse({"detail": "Invalid panel request"}, status_code=422)
        result = existing_validation(request, exc)
        return await result if isawaitable(result) else result

    app.include_router(router)
    app.include_router(status_router)
    app.include_router(features_router)
    app.include_router(search_router)
    app.include_router(memes_router)
    app.include_router(schedules_router)
    app.mount(
        "/webui", StaticFiles(directory=str(files("aling.webui").joinpath("static")), html=True)
    )
    search_policy = getattr(runtime, "search_policy", None)
    if search_policy is not None:
        search_policy.panel_auth = app.state.panel_auth
    search_origin = getattr(runtime.settings, "search_origin", "")
    app.state.panel_search_status = {
        "configured": bool(search_origin),
        "internal_reachable": None,
        "json_enabled": None,
        "upstream_results": None,
        "last_verified": None,
        "category": None,
        "result_count": 0,
        "code": "unknown",
        "exception_type": None,
    }
