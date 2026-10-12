import json
from time import monotonic
from urllib.parse import parse_qs

import httpx
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from pydantic import ValidationError

from aling.core.config import Settings
from aling.core.storage import DeploymentSearchPolicy, GroupSettingsStore, SearchSourceRule
from aling.services.features import FeatureService
from aling.services.search import SearchError, SearchHit, SearchService, SearxngClient
from aling.services.search_budget import TaskBudget, TaskBudgetLimits
from aling.services.search_policy import SearchPolicyService, SearchPurpose


def budget():
    return TaskBudget.start(monotonic(), TaskBudgetLimits())


async def test_search_posts_query_to_fixed_origin_without_url_leakage():
    captured = []

    async def handle(request):
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Python asyncio",
                        "url": "https://docs.python.org/3/library/asyncio.html",
                        "content": "Official documentation",
                    },
                    {
                        "title": "unapproved host",
                        "url": "https://attacker.invalid/",
                        "content": "untrusted",
                    },
                ]
            },
        )

    config = Settings(_env_file=None, onebot_access_token="test")
    client = SearxngClient(config, transport=httpx.MockTransport(handle))
    hits = await client.search(
        "unique-query-secret",
        category="general",
        language="auto",
        safe_search=2,
        limit=3,
        budget=budget(),
    )

    assert len(captured) == 1
    assert captured[0].method == "POST"
    assert str(captured[0].url) == "http://search:8080/search"
    assert "unique-query-secret" not in str(captured[0].url)
    form = parse_qs(captured[0].content.decode())
    assert form == {
        "q": ["unique-query-secret"],
        "categories": ["general"],
        "language": ["auto"],
        "safesearch": ["2"],
        "format": ["json"],
        "pageno": ["1"],
    }
    assert [(hit.title, hit.content) for hit in hits] == [
        ("Python asyncio", "Official documentation"),
        ("unapproved host", "untrusted"),
    ]


async def test_image_search_preserves_result_page_and_explicit_image_url():
    async def handle(_request):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "approved result",
                        "url": "https://gallery.example/post/1",
                        "img_src": "https://cdn.example/image/1.png",
                        "content": "static image",
                    },
                    {
                        "title": "no direct image",
                        "url": "https://gallery.example/post/2",
                        "content": "page only",
                    },
                ]
            },
        )

    client = SearxngClient(
        Settings(_env_file=None, onebot_access_token="test"),
        transport=httpx.MockTransport(handle),
    )
    hits = await client.search(
        "cat meme",
        category="images",
        language="auto",
        safe_search=2,
        limit=3,
        budget=budget(),
    )
    assert hits[0].url == "https://gallery.example/post/1"
    assert hits[0].image_url == "https://cdn.example/image/1.png"
    assert hits[1].url == "https://gallery.example/post/2"
    assert hits[1].image_url is None


@pytest.mark.parametrize(
    "status,body,code",
    [
        (403, "JSON disabled", "json_disabled"),
        (502, "private upstream details", "upstream_failed"),
        (200, "not json", "upstream_failed"),
        (200, '{"results":{}}', "upstream_failed"),
        (200, '{"results":[{"title":7,"url":"https://docs.python.org/3/"}]}', "upstream_failed"),
    ],
)
async def test_search_rejects_unavailable_or_malformed_upstream(status, body, code):
    async def handle(_request):
        return httpx.Response(status, text=body)

    client = SearxngClient(
        Settings(_env_file=None, onebot_access_token="test"),
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(SearchError) as caught:
        await client.search(
            "unique-private-query",
            category="general",
            language="auto",
            safe_search=2,
            limit=3,
            budget=budget(),
        )
    assert caught.value.code == code
    assert "unique-private-query" not in str(caught.value)
    assert "private upstream details" not in str(caught.value)


async def test_search_rejects_redirect_and_oversized_response():
    config = Settings(_env_file=None, onebot_access_token="test", search_max_bytes=1024)

    async def redirect(_request):
        return httpx.Response(302, headers={"Location": "http://attacker.invalid/"})

    async def oversized(_request):
        return httpx.Response(200, content=json.dumps({"results": [], "noise": "x" * 2000}))

    for handler in (redirect, oversized):
        client = SearxngClient(config, transport=httpx.MockTransport(handler))
        with pytest.raises(SearchError):
            await client.search(
                "private-query",
                category="general",
                language="auto",
                safe_search=2,
                limit=3,
                budget=budget(),
            )


async def test_search_rejects_service_parameter_injection_before_network():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    client = SearxngClient(
        Settings(_env_file=None, onebot_access_token="test"),
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(SearchError):
        await client.search(
            "site:attacker.invalid",
            category="general&engines=all",
            language="auto",
            safe_search=2,
            limit=3,
            budget=budget(),
        )
    assert requests == []


@pytest.mark.parametrize(
    "settings",
    [
        {"search_origin": "http://user:pass@search:8080"},
        {"search_origin": "http://search:8080/path"},
        {"search_origin": "http://search:8080/?q=secret"},
        {"search_origin": "ftp://search:8080"},
    ],
)
def test_search_origin_must_be_a_credential_free_http_origin(settings):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, onebot_access_token="test", **settings)


class FakeClassifier:
    async def classify(self, text, *, budget):
        assert text == "Python docs"
        return SearchPurpose.TECHNICAL


class FakeSearchClient:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    async def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        kwargs["budget"].reserve_external_operation("search")
        return self.hits


async def _search_fixture(tmp_path, *, hits=None, classifier=True):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        ai_api_key="fictional",
        ai_model="test-model",
        superusers={1},
        allowed_groups={34},
        database_path=tmp_path / "search.db",
    )
    store = GroupSettingsStore(settings.database_path, superusers={1})
    await store.initialize()
    await store.set_deployment_search_policy(
        DeploymentSearchPolicy(
            sources=(SearchSourceRule("python", "technical", "docs.python.org", "/3"),)
        )
    )
    await store.set_search_consent(1, True)
    features = FeatureService(settings, store)
    await features.initialize()
    for feature in ("ai", "search"):
        await features.set_global(1, feature, True)
        await store.set_feature_enabled(34, feature, True)
    policy = SearchPolicyService(features, classifier=FakeClassifier() if classifier else None)
    fake_client = FakeSearchClient(
        hits
        or [
            SearchHit(
                "Python asyncio",
                "https://docs.python.org/3/library/asyncio.html",
                "Official documentation",
            )
        ]
    )
    service = SearchService(policy, fake_client)
    event = GroupMessageEvent(
        time=0,
        self_id=99,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        user_id=12,
        group_id=34,
        message=Message("Python docs"),
        raw_message="Python docs",
        font=0,
        sender={"user_id": 12, "role": "member"},
    )
    return settings, store, features, policy, fake_client, service, event


async def test_search_service_uses_shared_gate_and_returns_only_approved_sources(tmp_path):
    settings, store, features, policy, client, service, event = await _search_fixture(
        tmp_path,
        hits=[
            SearchHit("Python asyncio", "https://docs.python.org/3/library/asyncio.html", "docs"),
            SearchHit("Other result", "https://attacker.invalid/phish", "untrusted"),
        ],
    )
    await features.set_global(1, "web", False)
    context = await policy.create_task_from_event(event)

    result = await service.search(context, "Python docs", policy.classifier, budget())

    assert len(result.results) == 1
    assert result.results[0].url == "https://docs.python.org/3/library/asyncio.html"
    assert result.results[0].source_id == "python"
    assert result.results[0].result_id
    assert client.calls[0][0] == "Python docs"
    assert client.calls[0][1]["category"] == "general"
    assert not await features.enabled(34, "web")


async def test_search_disabled_fails_before_classifier_or_network(tmp_path):
    _, _, features, policy, client, service, event = await _search_fixture(tmp_path)
    await features.set_global(1, "search", False)
    context = await policy.create_task_from_event(event)

    with pytest.raises(SearchError) as caught:
        await service.search(context, "Python docs", policy.classifier, budget())

    assert caught.value.code in {"source_not_allowed", "topic_not_allowed"}
    assert client.calls == []


async def test_missing_classifier_fails_closed_before_sending_query(tmp_path):
    _, _, _, policy, client, service, event = await _search_fixture(tmp_path, classifier=False)
    context = await policy.create_task_from_event(event)

    with pytest.raises(SearchError) as caught:
        await service.search(context, "Python docs", None, budget())

    assert caught.value.code == "classification_unavailable"
    assert client.calls == []


async def test_search_rejects_group_disallowed_by_deployment(tmp_path):
    _, _, _, policy, client, service, event = await _search_fixture(tmp_path)
    context = await policy.create_task_from_event(event)
    event.group_id = 35

    with pytest.raises(SearchError):
        await service.search(context, "Python docs", policy.classifier, budget())

    assert client.calls == []
