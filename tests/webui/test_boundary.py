import pytest

from .conftest import ORIGIN, TOKEN


def test_admin_boundary_rejects_cross_site_before_body(panel_client):
    result = panel_client.put(
        "/api/admin/features/global/web",
        content=b"x" * 16385,
        headers={"Origin": "https://untrusted.invalid"},
    )
    assert result.status_code == 403


@pytest.mark.parametrize(
    "headers",
    [{"Host": "untrusted.invalid"}, {"Origin": "http://localhost:8080"}, {"X-CSRF-Token": "wrong"}],
)
def test_management_write_requires_canonical_host_origin_and_csrf(panel_client, headers):
    assert (
        panel_client.put(
            "/api/admin/features/global/web", content=b"{}", headers=headers
        ).status_code
        == 403
    )


def test_forwarded_headers_never_authorize_origin_or_change_failure_source(panel_factory):
    client = panel_factory()
    for n in range(5):
        assert (
            client.post(
                "/api/admin/session",
                json={"token": "wrong"},
                headers={"Origin": ORIGIN, "X-Forwarded-For": f"192.0.2.{n}"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/admin/session",
            json={"token": TOKEN},
            headers={"Origin": ORIGIN, "X-Forwarded-For": "192.0.2.99"},
        ).status_code
        == 429
    )
    assert (
        client.post(
            "/api/admin/session",
            json={"token": TOKEN},
            headers={
                "Origin": "https://evil.invalid",
                "X-Forwarded-Host": "127.0.0.1:8080",
                "X-Forwarded-Proto": "http",
            },
        ).status_code
        == 403
    )


def test_normal_body_exact_size_and_oversize(panel_client):
    assert (
        panel_client.put("/api/admin/features/global/web", content=b"a" * 16384).json()["bytes"]
        == 16384
    )
    assert (
        panel_client.put("/api/admin/features/global/web", content=b"a" * 16385).status_code == 413
    )


def test_login_body_limit(panel_factory):
    client = panel_factory()
    assert (
        client.post(
            "/api/admin/session", content=b"a" * 1025, headers={"Origin": ORIGIN}
        ).status_code
        == 413
    )


def test_management_headers_do_not_limit_onebot_internal_host(panel_factory):
    assert panel_factory().get(
        "/onebot/v11/internal-probe", headers={"Host": "bot:8080"}
    ).json() == {"preserved": True}


async def asgi_request(
    app,
    *,
    body_chunks,
    method="PUT",
    path="/api/admin/features/global/web",
    headers=(),
    observe=None,
):
    reads = []
    messages = []
    chunks = iter(body_chunks)

    async def receive():
        reads.append(observe() if observe else True)
        chunk = next(chunks)
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:8080"), *headers],
            "client": ("127.0.0.1", 4444),
            "server": ("127.0.0.1", 8080),
        },
        receive,
        send,
    )
    return reads, messages


async def test_unauthenticated_request_does_not_read_any_business_body(panel_factory):
    reads, messages = await asgi_request(panel_factory().app, body_chunks=[])
    assert not reads
    assert messages[0]["status"] == 401


async def test_origin_failure_does_not_read_any_login_body(panel_factory):
    reads, messages = await asgi_request(
        panel_factory().app,
        method="POST",
        path="/api/admin/session",
        body_chunks=[],
        headers=[(b"origin", b"https://evil.invalid")],
    )
    assert not reads
    assert messages[0]["status"] == 403


async def test_no_content_length_chunked_body_is_limited_before_parsing(panel_client):
    reads, messages = await asgi_request(
        panel_client.app,
        body_chunks=[b"a" * 8000, b"b" * 8385],
        headers=[
            (b"origin", ORIGIN.encode()),
            (b"x-csrf-token", panel_client.headers["X-CSRF-Token"].encode()),
            (
                b"cookie",
                f"aling_panel_session={panel_client.cookies.get('aling_panel_session')}".encode(),
            ),
        ],
    )
    assert len(reads) == 2
    assert messages[0]["status"] == 413


async def test_rate_limited_login_does_not_read_body(panel_factory):
    client = panel_factory()
    for _ in range(5):
        client.post("/api/admin/session", json={"token": "wrong"}, headers={"Origin": ORIGIN})
    reads, messages = await asgi_request(
        client.app,
        method="POST",
        path="/api/admin/session",
        body_chunks=[],
        headers=[(b"origin", ORIGIN.encode())],
    )
    assert not reads
    assert messages[0]["status"] == 429


async def test_csrf_failure_does_not_read_body(panel_client):
    reads, messages = await asgi_request(
        panel_client.app,
        body_chunks=[],
        headers=[
            (b"origin", ORIGIN.encode()),
            (b"x-csrf-token", b"wrong"),
            (
                b"cookie",
                f"aling_panel_session={panel_client.cookies.get('aling_panel_session')}".encode(),
            ),
        ],
    )
    assert reads == []
    assert messages[0]["status"] == 403


async def test_declared_oversize_login_rejected_without_body(panel_factory):
    reads, messages = await asgi_request(
        panel_factory().app,
        method="POST",
        path="/api/admin/session",
        body_chunks=[],
        headers=[(b"origin", ORIGIN.encode()), (b"content-length", b"1025")],
    )
    assert reads == []
    assert messages[0]["status"] == 413


async def test_duplicate_host_rejected_without_body(panel_factory):
    reads, messages = await asgi_request(
        panel_factory().app,
        method="POST",
        path="/api/admin/session",
        body_chunks=[],
        headers=[(b"host", b"evil.invalid"), (b"origin", ORIGIN.encode())],
    )
    assert reads == []
    assert messages[0]["status"] == 403


def test_management_reads_require_canonical_host(panel_client):
    assert (
        panel_client.get("/api/admin/session", headers={"Host": "evil.invalid"}).status_code == 403
    )


def test_upload_uses_image_limit_and_still_requires_csrf(panel_client):
    assert (
        panel_client.post("/api/admin/memes/upload", content=b"a" * 16385).json()["bytes"] == 16385
    )
    assert panel_client.post("/api/admin/memes/upload", content=b"a" * 5000001).status_code == 413
    assert (
        panel_client.post(
            "/api/admin/memes/upload", content=b"a" * 5000001, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )


async def test_upload_limit_streams_after_route_can_claim_concurrency_slot(panel_client):
    app = panel_client.app
    reads, messages = await asgi_request(
        app,
        path="/api/admin/memes/upload",
        method="POST",
        body_chunks=[b"a" * 2500000, b"b" * 2500001],
        headers=[
            (b"origin", ORIGIN.encode()),
            (b"x-csrf-token", panel_client.headers["X-CSRF-Token"].encode()),
            (
                b"cookie",
                f"aling_panel_session={panel_client.cookies.get('aling_panel_session')}".encode(),
            ),
        ],
        observe=lambda: getattr(app.state, "upload_started", False),
    )
    assert reads == [True, True]
    assert messages[0]["status"] == 413
    assert (b"x-content-type-options", b"nosniff") in messages[0]["headers"]
