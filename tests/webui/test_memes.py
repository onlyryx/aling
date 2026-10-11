from io import BytesIO

import pytest
from PIL import Image

from tests.webui.conftest import ORIGIN, TOKEN


def picture():
    out = BytesIO()
    Image.new("RGB", (3, 3), "red").save(out, "PNG")
    return out.getvalue()


def test_meme_routes_upload_edit_preview_remove(runtime_panel):
    client, runtime = runtime_panel
    runtime.memes.root.mkdir()
    response = client.post("/api/admin/memes/upload", content=picture())
    assert response.status_code == 201
    resource = response.json()["id"]
    assert (
        client.patch(
            f"/api/admin/memes/{resource}",
            json=dict(description="猫", tags=["猫"], source="fictional", license="CC0"),
        ).status_code
        == 200
    )
    listing = client.get("/api/admin/memes?query=猫").json()
    assert listing["total"] == 1 and listing["write_status"]["writable"]
    assert "path" not in listing["items"][0]
    image = client.get(f"/api/admin/memes/{resource}/image")
    assert image.content == picture() and image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in image.headers["content-security-policy"]
    assert client.delete(f"/api/admin/memes/{resource}").json() == {"removed": True}
    assert client.get("/api/admin/memes").json()["total"] == 0
    assert list(runtime.memes.root.glob("upload-*"))
    assert client.get("/webui/pages/memes.js").status_code == 200


def test_readonly_and_safe_validation_errors(runtime_panel):
    client, runtime = runtime_panel
    assert client.get("/api/admin/memes").json()["write_status"]["reason"] == "missing_directory"
    response = client.post("/api/admin/memes/upload", content=picture())
    assert response.status_code == 409 and response.json()["detail"] == "meme_read_only"
    runtime.memes.root.mkdir()
    resource = client.post("/api/admin/memes/upload", content=picture()).json()["id"]
    response = client.patch(
        f"/api/admin/memes/{resource}", json=dict(description="x", tags=[], path="../private-key")
    )
    assert response.status_code == 422 and "private-key" not in response.text
    (runtime.memes.root / "manifest.json").write_text("private corrupt contents")
    response = client.get("/api/admin/memes")
    assert response.status_code == 422 and "private" not in response.text


def test_failed_uploads_count_and_next_attempt_denied(runtime_panel):
    client, runtime = runtime_panel
    runtime.memes.root.mkdir()
    for _ in range(30):
        assert client.post("/api/admin/memes/upload", content=b"invalid").status_code == 422
    assert client.post("/api/admin/memes/upload", content=picture()).status_code == 429
    # New login for the same owner does not reset upload admission.
    response = client.post("/api/admin/session", json={"token": TOKEN}, headers={"Origin": ORIGIN})
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert client.post("/api/admin/memes/upload", content=picture()).status_code == 429


async def test_upload_auth_and_concurrency_before_body_read(runtime_panel):
    from tests.webui.test_boundary import asgi_request

    client, runtime = runtime_panel
    runtime.memes.root.mkdir()
    reads, messages = await asgi_request(
        client.app,
        path="/api/admin/memes/upload",
        method="POST",
        body_chunks=[picture()],
        headers=[(b"origin", ORIGIN.encode())],
    )
    assert not reads and messages[0]["status"] == 401
    admission = client.app.state.panel_uploads
    admission.active = 2
    reads, messages = await asgi_request(
        client.app,
        path="/api/admin/memes/upload",
        method="POST",
        body_chunks=[picture()],
        headers=[
            (b"origin", ORIGIN.encode()),
            (b"x-csrf-token", client.headers["X-CSRF-Token"].encode()),
            (
                b"cookie",
                f"aling_panel_session={client.cookies.get('aling_panel_session')}".encode(),
            ),
        ],
    )
    assert not reads and messages[0]["status"] == 429
    admission.active = 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "x" * 201),
        ("tags", ["x"] * 33),
        ("source", "x" * 501),
        ("license", "x" * 501),
    ],
)
def test_metadata_validation_bounds(runtime_panel, field, value):
    client, _ = runtime_panel
    body = dict(description="x", tags=[], source="", license="")
    body[field] = value
    assert client.patch("/api/admin/memes/unknown", json=body).status_code == 422


def test_oversized_upload_failures_count_and_release_slot(runtime_panel):
    client, runtime = runtime_panel
    runtime.memes.root.mkdir()
    for _ in range(30):
        response = client.post("/api/admin/memes/upload", content=b"x" * 5_000_001)
        assert response.status_code == 413
        assert client.app.state.panel_uploads.active == 0
    assert client.post("/api/admin/memes/upload", content=picture()).status_code == 429


async def test_two_streams_hold_slots_and_third_is_denied_without_read(runtime_panel):
    import asyncio

    from tests.webui.test_boundary import asgi_request

    client, runtime = runtime_panel
    runtime.memes.root.mkdir()
    headers = [
        (b"origin", ORIGIN.encode()),
        (b"x-csrf-token", client.headers["X-CSRF-Token"].encode()),
        (b"cookie", f"aling_panel_session={client.cookies.get('aling_panel_session')}".encode()),
    ]
    release = asyncio.Event()
    entered = [asyncio.Event(), asyncio.Event()]

    def gated_app(index):
        async def wrapped(scope, receive, send):
            async def paused_receive():
                entered[index].set()
                await release.wait()
                message = await receive()
                message["more_body"] = False
                return message

            await client.app(scope, paused_receive, send)

        return wrapped

    tasks = [
        asyncio.create_task(
            asgi_request(
                gated_app(index),
                path="/api/admin/memes/upload",
                method="POST",
                body_chunks=[picture()],
                headers=headers,
            )
        )
        for index in range(2)
    ]
    try:
        await asyncio.gather(*(event.wait() for event in entered))
        reads, messages = await asgi_request(
            client.app,
            path="/api/admin/memes/upload",
            method="POST",
            body_chunks=[picture()],
            headers=headers,
        )
        assert not reads and messages[0]["status"] == 429
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert all(messages[0]["status"] == 201 for _, messages in results)
    assert client.app.state.panel_uploads.active == 0


def test_upload_attempt_window_expires():
    from fastapi import HTTPException

    from aling.webui.routes_memes import UploadAdmission

    now = [0]
    admission = UploadAdmission(clock=lambda: now[0])
    for _ in range(30):
        admission.acquire(12345)
        admission.release()
    with pytest.raises(HTTPException):
        admission.acquire(12345)
    now[0] = 600
    admission.acquire(12345)
    admission.release()


@pytest.mark.parametrize(
    "length_headers",
    [
        [(b"content-length", b"invalid")],
        [(b"content-length", b"83"), (b"content-length", b"83")],
        [(b"content-length", b"83"), (b"content-length", b"5000001")],
    ],
    ids=["malformed", "duplicate", "conflicting"],
)
async def test_invalid_upload_lengths_count_once_without_body_read(runtime_panel, length_headers):
    from tests.webui.test_boundary import asgi_request

    client, _ = runtime_panel
    admission = client.app.state.panel_uploads
    headers = [
        (b"origin", ORIGIN.encode()),
        (b"x-csrf-token", client.headers["X-CSRF-Token"].encode()),
        (b"cookie", f"aling_panel_session={client.cookies.get('aling_panel_session')}".encode()),
        *length_headers,
    ]
    for attempt in range(30):
        reads, messages = await asgi_request(
            client.app,
            path="/api/admin/memes/upload",
            method="POST",
            body_chunks=[],
            headers=headers,
        )
        assert not reads and messages[0]["status"] == 400
        assert len(admission.attempts.get(12345, ())) == attempt + 1
        assert admission.active == 0
    reads, messages = await asgi_request(
        client.app, path="/api/admin/memes/upload", method="POST", body_chunks=[], headers=headers
    )
    assert not reads and messages[0]["status"] == 429
    assert len(admission.attempts[12345]) == 30 and admission.active == 0
    assert client.post("/api/admin/memes/upload", content=picture()).status_code == 429


@pytest.mark.parametrize("boundary_failure", ["session", "host", "origin", "csrf"])
async def test_invalid_upload_length_outside_trust_boundary_is_not_counted(
    runtime_panel, boundary_failure
):
    from tests.webui.test_boundary import asgi_request

    client, _ = runtime_panel
    headers = {
        b"origin": ORIGIN.encode(),
        b"x-csrf-token": client.headers["X-CSRF-Token"].encode(),
        b"cookie": f"aling_panel_session={client.cookies.get('aling_panel_session')}".encode(),
        b"content-length": b"invalid",
    }
    if boundary_failure == "session":
        del headers[b"cookie"]
    elif boundary_failure == "host":
        headers[b"host"] = b"untrusted.invalid"
    elif boundary_failure == "origin":
        headers[b"origin"] = b"https://untrusted.invalid"
    else:
        headers[b"x-csrf-token"] = b"invalid"
    reads, messages = await asgi_request(
        client.app,
        path="/api/admin/memes/upload",
        method="POST",
        body_chunks=[],
        headers=list(headers.items()),
    )
    assert not reads
    assert messages[0]["status"] == (401 if boundary_failure == "session" else 403)
    assert client.app.state.panel_uploads.attempts == {}
    assert client.app.state.panel_uploads.active == 0
