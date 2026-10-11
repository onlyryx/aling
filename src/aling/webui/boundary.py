"""Admin-only ASGI checks, completed before a business route can read its body."""

import re
from secrets import compare_digest
from urllib.parse import urlsplit

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from aling.webui.auth import COOKIE_NAME, LoginRateLimited, PanelAuth

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def is_admin_path(path: str) -> bool:
    return path == "/api/admin" or path.startswith("/api/admin/")


class AdminBoundaryMiddleware:
    def __init__(self, app: ASGIApp, *, auth: PanelAuth, origin: str):
        self.app = app
        self.auth = auth
        self.origin = origin
        self.host = urlsplit(origin).netloc

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        admin = is_admin_path(path)
        shell = path == "/webui" or path.startswith("/webui/")
        if scope["type"] not in {"http", "websocket"} or not (admin or shell):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = SECURITY_HEADERS.copy()
                if scope["method"] == "GET" and re.fullmatch(
                    r"/api/admin/memes/[a-zA-Z0-9_-]{1,64}/image", path
                ):
                    headers["Content-Security-Policy"] = (
                        "default-src 'none'; frame-ancestors 'none'; sandbox"
                    )
                keys = {key.lower().encode() for key in headers}
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in keys
                ] + [(key.lower().encode(), value.encode()) for key, value in headers.items()]
            await send(message)

        async def reject(status: int, detail: str):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, secure_send)

        if not admin:
            await self.app(scope, receive, secure_send)
            return

        def header(name: bytes) -> str | None:
            values = [
                value.decode("latin-1") for key, value in scope["headers"] if key.lower() == name
            ]
            return values[0] if len(values) == 1 else None

        if header(b"host") != self.host:
            await reject(403, "Invalid panel host")
            return
        login = path == "/api/admin/session" and scope["method"] == "POST"
        writing = scope["method"] not in {"GET", "HEAD"}
        if not login:
            try:
                # Do not merge ambiguous Cookie headers into an authenticated credential.
                if header(b"cookie") is None:
                    raise PermissionError
                session = self.auth.authenticate(Request(scope).cookies.get(COOKIE_NAME, ""))
            except PermissionError:
                await reject(401, "Panel session required")
                return
            scope.setdefault("state", {})["panel_session"] = session
        if writing:
            if header(b"origin") != self.origin:
                await reject(403, "Invalid panel origin")
                return
            if not login and not compare_digest(
                (header(b"x-csrf-token") or "").encode(), session.csrf_token.encode()
            ):
                await reject(403, "Invalid CSRF token")
                return
        if login:
            client_ip = scope.get("client")[0] if scope.get("client") else "unknown"
            try:
                self.auth.check_login_allowed(client_ip)
            except LoginRateLimited:
                await reject(429, "Login rate limit reached")
                return
        limit = 1024 if login else 16384
        upload = path == "/api/admin/memes/upload" and scope["method"] == "POST"
        declared_error = None
        if upload:
            limit = 5_000_000
        lengths = [value for key, value in scope["headers"] if key.lower() == b"content-length"]
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                declared_error = (400, "Invalid content length")
            else:
                # Compare length lexically to avoid unbounded integer parsing.
                length = lengths[0].lstrip(b"0") or b"0"
                maximum = str(limit).encode()
                if len(length) > len(maximum) or (len(length) == len(maximum) and length > maximum):
                    declared_error = (413, "Panel request body too large")
            if declared_error is not None and not upload:
                await reject(*declared_error)
                return
        if upload:
            # Let the upload route acquire its concurrency slot before reading image bytes.
            received = 0

            async def image_receive():
                nonlocal received
                if declared_error is not None:
                    raise HTTPException(*declared_error)
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit:
                        raise HTTPException(413, "Panel request body too large")
                return message

            await self.app(scope, image_receive, secure_send)
            return
        # Buffer only bounded input. No route or parser sees partial oversized input.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                await reject(413, "Panel request body too large")
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, secure_send)
