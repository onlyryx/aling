"""Login routes use fixed errors so validation never serializes submitted keys."""

import json

from fastapi import APIRouter, Request, Response
from starlette.responses import JSONResponse

from aling.webui.auth import COOKIE_NAME, SESSION_SECONDS, LoginRateLimited, PanelSession

router = APIRouter(prefix="/api/admin")


def session_payload(session: PanelSession) -> dict[str, int | str]:
    return {"owner_id": session.owner_id, "csrf_token": session.csrf_token}


@router.post("/session")
async def login(request: Request):
    auth = request.app.state.panel_auth
    client_ip = request.client.host if request.client else "unknown"
    try:
        body = await request.json()
        if (
            not isinstance(body, dict)
            or set(body) != {"token"}
            or not isinstance(body["token"], str)
        ):
            auth.record_failure(client_ip)
            return JSONResponse({"detail": "Invalid login credentials"}, status_code=401)
        session = auth.login(body["token"], client_ip)
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            auth.record_failure(client_ip)
        except LoginRateLimited:
            return JSONResponse({"detail": "Login rate limit reached"}, status_code=429)
        return JSONResponse({"detail": "Invalid login credentials"}, status_code=401)
    except LoginRateLimited:
        return JSONResponse({"detail": "Login rate limit reached"}, status_code=429)
    except PermissionError:
        return JSONResponse({"detail": "Invalid login credentials"}, status_code=401)
    response = JSONResponse(session_payload(session))
    response.set_cookie(
        COOKIE_NAME,
        session.session_id,
        max_age=SESSION_SECONDS,
        path="/api/admin",
        secure=request.app.state.panel_runtime.settings.webui_origin.startswith("https://"),
        httponly=True,
        samesite="strict",
    )
    return response


@router.get("/session")
async def current_session(request: Request):
    return session_payload(request.state.panel_session)


@router.delete("/session", status_code=204)
async def logout(request: Request):
    request.app.state.panel_auth.revoke(request.state.panel_session.session_id)
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME,
        path="/api/admin",
        secure=request.app.state.panel_runtime.settings.webui_origin.startswith("https://"),
        httponly=True,
        samesite="strict",
    )
    return response
