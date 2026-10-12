import json
from urllib.parse import parse_qs

import httpx

from aling.core.storage import (
    DeploymentSearchPolicy,
    SearchSourceRule,
)
from aling.services.search_policy import SearchPurpose


def test_policy_api_is_owner_authenticated_strict_and_persisted(runtime_panel):
    client, runtime = runtime_panel
    assert client.get("/api/admin/search-policy").status_code == 200
    current = client.get("/api/admin/search-policy").json()
    assert current["allowed_purposes"] == ["ordinary_meme", "technical"]
    assert current["sources"] == []

    policy = {
        "allowed_purposes": ["technical"],
        "sources": [
            {
                "source_id": "docs",
                "purpose": "technical",
                "host": "docs.example.org",
                "path_prefix": "/manual/",
                "kind": "page",
                "port": None,
                "allowed_query_params": [],
                "scheme": "https",
            }
        ],
        "limits": {"task_seconds": 45, "work_seconds": 42},
    }
    saved = client.put("/api/admin/search-policy", json=policy)
    assert saved.status_code == 200
    assert saved.json()["limits"]["task_seconds"] == 45
    assert "revision" in saved.json()
    assert "owner_id" not in saved.json()

    assert (
        client.put(
            "/api/admin/search-policy",
            json={**policy, "owner_id": 99, "role": "owner"},
        ).status_code
        == 422
    )
    assert (
        client.put("/api/admin/search-policy", json={**policy, "enabled": True}).status_code == 422
    )
    assert saved.json()["sources"][0]["host"] == "docs.example.org"
    assert runtime.store is not None


def test_group_policy_cannot_widen_owner_purposes_sources_or_limits(runtime_panel):
    client, runtime = runtime_panel

    async def seed():
        await runtime.store.set_deployment_search_policy(
            DeploymentSearchPolicy(
                allowed_purposes=frozenset({"technical"}),
                sources=(SearchSourceRule("docs", "technical", "docs.example.org"),),
            )
        )

    client.portal.call(seed)
    body = {
        "allowed_purposes": ["gaming"],
        "source_ids": ["other"],
        "limits": {"task_seconds": 60},
        "meme_mode": "online",
    }
    denied = client.put("/api/admin/groups/34/search-policy", json=body)
    assert denied.status_code == 422

    narrow = client.put(
        "/api/admin/groups/34/search-policy",
        json={"allowed_purposes": ["technical"], "source_ids": ["docs"], "meme_mode": "local"},
    )
    assert narrow.status_code == 200
    assert narrow.json()["source_ids"] == ["docs"]
    assert client.get("/api/admin/groups/56/search-policy").status_code == 403


def test_search_consent_is_separate_from_feature_activation(runtime_panel):
    client, _ = runtime_panel
    assert (
        client.put("/api/admin/features/global/search", json={"enabled": True}).status_code == 409
    )
    result = client.post("/api/admin/search-consent", json={"acknowledged": True})
    assert result.status_code == 200
    assert result.json() == {"acknowledged": True}
    assert (
        client.put("/api/admin/features/global/search", json={"enabled": True}).status_code == 200
    )
    assert (
        client.post(
            "/api/admin/search-consent", json={"acknowledged": True, "owner_id": 9}
        ).status_code
        == 422
    )
    assert client.post("/api/admin/search-consent", json={"acknowledged": 1}).status_code == 422


def test_probe_obeys_gates_and_success_contains_no_query_or_results(runtime_panel, monkeypatch):
    client, runtime = runtime_panel
    calls = {"classify": 0, "search": 0}
    decision = {"purpose": SearchPurpose.TECHNICAL}

    class Classifier:
        async def classify(self, text, *, budget=None):
            calls["classify"] += 1
            assert text == "python documentation"
            return decision["purpose"]

    async def transport(request):
        calls["search"] += 1
        assert request.url.path == "/search"
        assert request.method == "POST"
        assert parse_qs(request.content.decode())["q"] == ["python documentation"]
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "results": [
                    {
                        "title": "Python docs",
                        "url": "https://docs.example.org/manual/start",
                        "content": "Python reference",
                    }
                ]
            },
        )

    runtime.search_policy.classifier = Classifier()
    runtime.search_client._client._transport = httpx.MockTransport(transport)

    denied = client.post("/api/admin/search-probe", json={"group_id": 34})
    assert denied.status_code == 409
    assert calls == {"classify": 0, "search": 0}

    async def configure():
        await runtime.store.set_search_consent(12345, True)
        await runtime.store.set_deployment_search_policy(
            DeploymentSearchPolicy(
                allowed_purposes=frozenset({"technical"}),
                sources=(SearchSourceRule("docs", "technical", "docs.example.org", "/manual/"),),
            )
        )

    client.portal.call(configure)
    for feature in ("ai", "search"):
        assert (
            client.put(f"/api/admin/features/global/{feature}", json={"enabled": True}).status_code
            == 200
        )
        assert (
            client.put(
                f"/api/admin/groups/34/features/{feature}", json={"enabled": True}
            ).status_code
            == 200
        )

    result = client.post("/api/admin/search-probe", json={"group_id": 34})
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["upstream_results"] is True
    assert payload["result_count"] == 1
    assert payload["category"] == "general"
    assert payload["last_verified"]
    assert "python documentation" not in json.dumps(payload)
    assert "docs.example.org" not in json.dumps(payload)
    assert calls == {"classify": 1, "search": 1}


def test_policy_write_requires_csrf_and_canonical_origin(runtime_panel):
    client, _ = runtime_panel
    headers = {"Origin": "https://foreign.invalid"}
    response = client.put("/api/admin/search-policy", json={}, headers=headers)
    assert response.status_code == 403

    csrf = client.headers.pop("X-CSRF-Token")
    try:
        response = client.put("/api/admin/search-policy", json={})
        assert response.status_code == 403
    finally:
        client.headers["X-CSRF-Token"] = csrf


def test_policy_api_rejects_unauthenticated_and_over_maximum_quota(panel_factory, runtime_panel):
    client = panel_factory()
    assert client.get("/api/admin/search-policy").status_code == 401
    assert (
        client.put(
            "/api/admin/search-policy",
            json={"allowed_purposes": ["technical"]},
            headers={"Origin": "http://127.0.0.1:8080"},
        ).status_code
        == 401
    )

    client, _ = runtime_panel
    assert (
        client.put(
            "/api/admin/search-policy",
            json={
                "allowed_purposes": ["technical"],
                "sources": [],
                "limits": {"task_seconds": 61},
            },
        ).status_code
        == 422
    )


def test_probe_rejects_prohibited_purpose_before_search(runtime_panel, monkeypatch):
    client, runtime = runtime_panel

    class Classifier:
        async def classify(self, text, *, budget=None):
            return SearchPurpose.GAMING

    calls = 0

    async def search(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("prohibited purpose must not reach SearXNG")

    runtime.search_policy.classifier = Classifier()
    monkeypatch.setattr(runtime.search.client, "search", search)

    async def configure():
        await runtime.store.set_search_consent(12345, True)
        await runtime.store.set_deployment_search_policy(
            DeploymentSearchPolicy(
                allowed_purposes=frozenset({"technical"}),
                sources=(SearchSourceRule("docs", "technical", "docs.example.org"),),
            )
        )

    client.portal.call(configure)
    for feature in ("ai", "search"):
        client.put(f"/api/admin/features/global/{feature}", json={"enabled": True})
        client.put(f"/api/admin/groups/34/features/{feature}", json={"enabled": True})
    result = client.post("/api/admin/search-probe", json={"group_id": 34})
    assert result.status_code == 409
    assert result.json() == {"detail": "search_policy_unavailable"}
    assert calls == 0


def test_policy_and_status_gets_never_call_classifier_or_search(runtime_panel, monkeypatch):
    client, runtime = runtime_panel

    async def unexpected(*args, **kwargs):
        raise AssertionError("GET policy/status must not make network or model calls")

    monkeypatch.setattr(runtime.search_client, "search", unexpected)
    monkeypatch.setattr(runtime.search_policy.classifier, "classify", unexpected)
    assert client.get("/api/admin/search-policy").status_code == 200
    assert client.get("/api/admin/search-status").status_code == 200


def test_policy_update_waits_for_task_cancellation_and_cache_cleanup(runtime_panel, monkeypatch):
    client, runtime = runtime_panel
    order = []

    async def cancel(group_id=None):
        order.append(("cancel", group_id))

    async def cleanup():
        order.append(("cleanup", None))
        return 0

    monkeypatch.setattr(runtime.search_policy, "revoke_group_tasks", cancel)
    monkeypatch.setattr(runtime.online_memes, "expire", cleanup)
    result = client.put(
        "/api/admin/groups/34/search-policy",
        json={"allowed_purposes": ["technical"], "source_ids": [], "meme_mode": "local"},
    )
    assert result.status_code == 200
    assert order == [("cancel", 34), ("cleanup", None)]


def test_group_policy_tightening_cancels_bound_panel_task(runtime_panel):
    import asyncio

    client, runtime = runtime_panel
    from aling.webui.auth import COOKIE_NAME

    session = client.app.state.panel_auth.authenticate(client.cookies.get(COOKIE_NAME))
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run_probe():
        await runtime.search_policy.create_task_from_panel(session, 34)
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()

    async def start():
        task = asyncio.create_task(run_probe())
        await started.wait()
        return task

    task = client.portal.call(start)
    response = client.put(
        "/api/admin/groups/34/search-policy",
        json={"allowed_purposes": [], "source_ids": [], "meme_mode": "local"},
    )
    assert response.status_code == 200
    assert client.portal.call(cancelled.is_set)
    assert client.portal.call(task.done)


def test_feature_disable_uses_shared_task_and_cache_revocation(runtime_panel, monkeypatch):
    client, runtime = runtime_panel
    calls = []

    async def revoke(group_id):
        calls.append(("revoke", group_id))

    async def cleanup():
        calls.append(("cleanup", None))
        return 0

    monkeypatch.setattr(runtime.search_policy, "revoke_group_tasks", revoke)
    monkeypatch.setattr(runtime.online_memes, "expire", cleanup)
    response = client.put("/api/admin/groups/34/features/search", json={"enabled": False})
    assert response.status_code == 200
    assert calls == [("revoke", 34), ("cleanup", None)]
