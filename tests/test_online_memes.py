import asyncio
from io import BytesIO
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from PIL import Image

from aling.core.config import Settings
from aling.core.storage import DeploymentSearchPolicy, GroupSettingsStore, SearchSourceRule
from aling.services.features import FeatureService
from aling.services.online_memes import (
    MemeImageClient,
    OnlineMemeService,
    TemporaryMemeRef,
    TemporaryMemeStore,
)
from aling.services.search import SearchHit
from aling.services.search_budget import TaskBudget
from aling.services.search_policy import SearchPolicyService, SearchPurpose


def png_bytes(size=(8, 8), *, format="PNG", frames=1):
    images = [Image.new("RGB", size, color) for color in ("red", "blue")[:frames]]
    output = BytesIO()
    images[0].save(output, format, save_all=frames > 1, append_images=images[1:])
    return output.getvalue()


def fake_context(group_id=34, user_id=12, task_id="task-a", revision=(1, 1)):
    return SimpleNamespace(
        group_id=group_id,
        user_id=user_id,
        task_id=task_id,
        policy_revision=revision,
        purpose=SearchPurpose.ORDINARY_MEME,
        source_ids=frozenset({"media"}),
    )


async def allow_policy(_):
    return True


async def deny_page(_):
    return False


class FakePolicy:
    def __init__(self):
        self.revoked = False
        self.urls = {}
        self.authorized = []
        self.store = SimpleNamespace(
            get_deployment_search_policy=lambda: asyncio.sleep(
                0,
                result=SimpleNamespace(
                    sources=(
                        SimpleNamespace(kind="page", purpose="ordinary_meme", source_id="page"),
                        SimpleNamespace(kind="image", purpose="ordinary_meme", source_id="media"),
                    ),
                    limits=SimpleNamespace(
                        task_seconds=60,
                        work_seconds=57,
                        max_tool_rounds=3,
                        max_model_requests=5,
                        max_external_operations=4,
                        max_searches=1,
                        max_results=3,
                        request_timeout_seconds=10,
                        instance_concurrency=2,
                        group_concurrency=1,
                        user_cooldown_seconds=15,
                        user_hourly_limit=10,
                        group_hourly_limit=30,
                        instance_hourly_limit=100,
                    ),
                ),
            ),
            get_group_search_policy=lambda _group_id: asyncio.sleep(
                0, result=SimpleNamespace(source_ids=None, limits=None)
            ),
        )

    async def authorize_online(self, context, query, operation, *, budget=None):
        assert operation == "online_meme"
        self.authorized.append((context, query))
        return SimpleNamespace(**vars(context), normalized_query=query)

    async def validate_result_url(self, url, purpose, source_id):
        valid = {
            "media": url.startswith("https://media.example/"),
            "page": url.startswith("https://page.example/"),
        }
        if not valid.get(source_id, False):
            raise PermissionError("unapproved source")
        return url

    async def register_result(self, context, url, source_id):
        await self.validate_result_url(url, context.purpose, source_id)
        result_id = f"r{len(self.urls)}"
        self.urls[result_id] = url
        return result_id

    async def resolve_result(self, context, result_id, *, operation="read"):
        assert operation == "image"
        if result_id not in self.urls:
            raise PermissionError("missing result")
        await self.revalidate(context, "image", "media")
        return self.urls[result_id]

    async def revalidate(self, context, operation, source_id=None):
        if (
            self.revoked
            or operation not in {"image", "online_meme"}
            or source_id not in {None, "media", *self.urls}
        ):
            raise PermissionError("policy revoked")


class FakeSearch:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    async def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        kwargs["budget"].reserve_external_operation("search")
        return self.hits


class FakeLocalMemes:
    def __init__(self, matches=()):
        self.matches = list(matches)
        self.calls = []

    async def search(self, query, *, limit=1):
        self.calls.append(query)
        return self.matches[:limit]


async def test_local_mode_uses_local_catalog_without_policy_classifier_or_network():
    local = FakeLocalMemes([SimpleNamespace(resource_id="local-1", description="本地猫猫")])
    policy, search = FakePolicy(), FakeSearch([])
    service = OnlineMemeService(local, policy, search, MemeImageClient(resolver=never_resolve))

    result = await service.search_and_download(fake_context(), "猫猫", "local", TaskBudget.start())

    assert result.resource_id == "local-1"
    assert policy.authorized == []
    assert search.calls == []


async def test_auto_uses_local_hit_without_classification_or_external_calls():
    local = FakeLocalMemes([SimpleNamespace(resource_id="local-1", description="本地猫猫")])
    policy, search = FakePolicy(), FakeSearch([])
    service = OnlineMemeService(local, policy, search, MemeImageClient(resolver=never_resolve))

    result = await service.search_and_download(fake_context(), "猫猫", "auto", TaskBudget.start())

    assert result.resource_id == "local-1"
    assert policy.authorized == []
    assert search.calls == []


async def test_auto_miss_authorizes_online_only_after_local_miss():
    local, policy, search = FakeLocalMemes(), FakePolicy(), FakeSearch([])
    service = OnlineMemeService(local, policy, search, MemeImageClient(resolver=never_resolve))

    assert (
        await service.search_and_download(fake_context(), "猫猫", "auto", TaskBudget.start())
        is None
    )

    assert local.calls == ["猫猫"]
    assert policy.authorized == [(policy.authorized[0][0], "猫猫")]
    assert len(search.calls) == 1


async def test_temporary_store_binds_resource_to_principal_and_task_and_expires():
    now = [100.0]
    store = TemporaryMemeStore(clock=lambda: now[0])
    content = png_bytes()
    ref = await store.put(34, 12, "task-a", "media", (1, 1), content, 400)
    assert len(ref.opaque_id) >= 22
    assert (await store.read_for_send(ref.opaque_id, 34, 12, allow_policy)).content == content
    with pytest.raises(PermissionError):
        await store.read_for_send(ref.opaque_id, 35, 12, allow_policy)
    with pytest.raises(PermissionError):
        await store.read_for_send(ref.opaque_id, 34, 13, allow_policy)
    now[0] = 400
    with pytest.raises(PermissionError):
        await store.read_for_send(ref.opaque_id, 34, 12, allow_policy)
    assert await store.size() == (0, 0)


async def test_temporary_store_enforces_item_and_byte_budget_including_reservations():
    store = TemporaryMemeStore(max_items=1, max_bytes=128)
    reservation = await store.reserve(100)
    with pytest.raises(RuntimeError):
        await store.reserve(1)
    store.release(reservation)
    ref = await store.put(34, 12, "task-a", "media", (1, 1), png_bytes(), 9999999999)
    assert ref.opaque_id
    with pytest.raises(RuntimeError):
        await store.put(34, 12, "task-b", "media", (1, 1), png_bytes(), 9999999999)


def test_online_cache_and_downloader_limits_cannot_be_configured_above_hard_caps():
    store = TemporaryMemeStore(max_items=100, max_bytes=100_000_000, ttl_seconds=3600)
    client = MemeImageClient(timeout=60, max_bytes=100_000_000, max_pixels=100_000_000)

    assert (store.max_items, store.max_bytes, store.ttl_seconds) == (20, 50_000_000, 300)
    assert (client.timeout, client.max_bytes, client.max_pixels) == (10, 5_000_000, 12_000_000)


async def test_temporary_store_policy_revoke_and_clear_remove_resources():
    store = TemporaryMemeStore()
    first = await store.put(34, 12, "task-a", "media", (1, 1), png_bytes(), 9999999999)
    second = await store.put(34, 12, "task-b", "other", (1, 2), png_bytes(), 9999999999)
    assert await store.revoke_policy(34, (1, 2), {"other"}) == 1
    assert not await store.contains(first.opaque_id)
    assert await store.contains(second.opaque_id)
    await store.clear()
    assert await store.size() == (0, 0)


async def test_image_client_rejects_mixed_public_and_private_dns_answers():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})

    client = MemeImageClient(
        transport=httpx.MockTransport(handler),
        resolver=lambda *_: asyncio.sleep(0, result=["8.8.8.8", "127.0.0.1"]),
    )
    with pytest.raises(ValueError):
        await client.fetch("https://media.example/image.png")
    assert calls == []


@pytest.mark.parametrize(
    "url,headers,body",
    [
        ("https://media.example:8443/img.png", {"content-type": "image/png"}, png_bytes()),
        ("https://media.example/img.png", {"content-type": "image/svg+xml"}, b"<svg/>"),
        ("https://media.example/img.png", {"content-type": "image/png"}, b"<html>fake</html>"),
        ("https://media.example/img.png", {"content-type": "image/jpeg"}, png_bytes()),
        ("https://media.example/img.gif", {"content-type": "image/gif"}, png_bytes(format="GIF")),
        (
            "https://media.example/animated.webp",
            {"content-type": "image/webp"},
            png_bytes(format="WEBP", frames=2),
        ),
        (
            "https://media.example/large.png",
            {"content-type": "image/png"},
            png_bytes(size=(4001, 3000)),
        ),
    ],
)
async def test_image_client_rejects_unsafe_port_or_declared_and_decoded_format(url, headers, body):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, content=body, headers=headers)

    client = MemeImageClient(
        transport=httpx.MockTransport(handler),
        resolver=lambda *_: asyncio.sleep(0, result=["8.8.8.8"]),
    )
    with pytest.raises(ValueError):
        await client.fetch(url)
    assert len(requests) <= 1


async def test_image_client_rejects_redirects_and_oversized_payload():
    async def redirect(_request):
        return httpx.Response(302, headers={"location": "https://evil.example/image.png"})

    async def oversized(_request):
        return httpx.Response(200, content=b"x" * 100, headers={"content-type": "image/png"})

    for handler, max_bytes in ((redirect, 1000), (oversized, 32)):
        client = MemeImageClient(
            transport=httpx.MockTransport(handler),
            resolver=lambda *_: asyncio.sleep(0, result=["8.8.8.8"]),
            max_bytes=max_bytes,
        )
        with pytest.raises(ValueError):
            await client.fetch("https://media.example/image.png")


async def test_image_client_times_out_within_remaining_task_budget():
    async def slow(_request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})

    client = MemeImageClient(
        timeout=0.001,
        transport=httpx.MockTransport(slow),
        resolver=lambda *_: asyncio.sleep(0, result=["8.8.8.8"]),
    )
    with pytest.raises(TimeoutError):
        await client.fetch("https://media.example/image.png")

    expired = TaskBudget.start(now=monotonic() - 60)
    with pytest.raises(TimeoutError):
        await client.fetch("https://media.example/image.png", budget=expired)
    assert expired._counts == {}


async def test_online_mode_searches_and_returns_only_validated_temporary_image():
    image = png_bytes()
    requested = []

    async def handler(request):
        requested.append(request)
        return httpx.Response(200, content=image, headers={"content-type": "image/png"})

    context = fake_context()
    policy = FakePolicy()
    search = FakeSearch(
        [SearchHit("cat", "https://page.example/cat", "", "https://media.example/cat.png")]
    )
    store = TemporaryMemeStore()
    service = OnlineMemeService(
        FakeLocalMemes([SimpleNamespace(resource_id="local", description="local")]),
        policy,
        search,
        MemeImageClient(
            transport=httpx.MockTransport(handler),
            resolver=lambda *_: asyncio.sleep(0, result=["8.8.8.8"]),
        ),
        store=store,
        page_source_validator=lambda _: asyncio.sleep(0, result=True),
    )
    budget = TaskBudget.start()

    ref = await service.search_and_download(context, "猫猫表情", "online", budget)

    assert ref.source_id == "media"
    assert len(requested) == 1
    assert str(requested[0].url) == "https://8.8.8.8/cat.png"
    assert requested[0].headers["host"] == "media.example"
    assert requested[0].extensions["sni_hostname"] == "media.example"
    assert len(search.calls) == 1
    assert search.calls[0][1]["category"] == "images"
    assert budget._counts == {"search": 1, "image": 1}
    assert await store.contains(ref.opaque_id)
    assert (await store.read_for_send(ref.opaque_id, 34, 12, allow_policy)).content == image
    with pytest.raises(PermissionError):
        await service.read_for_send(ref.opaque_id, 34, 12, task_id="other-task")


async def test_online_result_requires_both_approved_page_and_image_source():
    local, policy, search = (
        FakeLocalMemes(),
        FakePolicy(),
        FakeSearch(
            [
                SearchHit(
                    "cat", "https://unapproved.example/cat", "", "https://media.example/cat.png"
                )
            ]
        ),
    )
    service = OnlineMemeService(
        local,
        policy,
        search,
        MemeImageClient(resolver=never_resolve),
        page_source_validator=deny_page,
    )
    assert (
        await service.search_and_download(fake_context(), "猫猫", "online", TaskBudget.start())
        is None
    )
    assert policy.authorized


async def test_unapproved_image_cdn_is_skipped_even_if_result_page_is_approved():
    local, policy, search = (
        FakeLocalMemes(),
        FakePolicy(),
        FakeSearch(
            [SearchHit("cat", "https://page.example/cat", "", "https://cdn.example/cat.png")]
        ),
    )
    service = OnlineMemeService(
        local,
        policy,
        search,
        MemeImageClient(resolver=never_resolve),
        page_source_validator=allow_policy,
    )
    assert (
        await service.search_and_download(fake_context(), "猫猫", "online", TaskBudget.start())
        is None
    )


async def test_nonstandard_result_page_port_is_rejected_before_policy_or_download():
    local, policy, search = (
        FakeLocalMemes(),
        FakePolicy(),
        FakeSearch(
            [SearchHit("cat", "https://page.example:8443/cat", "", "https://media.example/cat.png")]
        ),
    )
    service = OnlineMemeService(
        local,
        policy,
        search,
        MemeImageClient(resolver=never_resolve),
        page_source_validator=allow_policy,
    )
    assert (
        await service.search_and_download(fake_context(), "猫猫", "online", TaskBudget.start())
        is None
    )


async def test_online_policy_revoked_before_cache_insertion_discards_download():
    image = png_bytes()

    async def handler(_request):
        policy.revoked = True
        return httpx.Response(200, content=image, headers={"content-type": "image/png"})

    policy = FakePolicy()
    service = OnlineMemeService(
        FakeLocalMemes(),
        policy,
        FakeSearch(
            [SearchHit("cat", "https://page.example/cat", "", "https://media.example/a.png")]
        ),
        MemeImageClient(
            transport=httpx.MockTransport(handler),
            resolver=lambda *_: asyncio.sleep(0, result=["8.8.8.8"]),
        ),
        store=TemporaryMemeStore(),
        page_source_validator=lambda _: asyncio.sleep(0, result=True),
    )
    with pytest.raises(PermissionError):
        await service.search_and_download(fake_context(), "猫猫", "online", TaskBudget.start())
    assert await service.store.size() == (0, 0)
    assert not service.store._reservations


async def test_real_search_policy_requires_approved_page_and_image_hosts(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="fictional",
        ai_api_key="fictional",
        ai_model="test",
        allowed_groups={34},
        superusers={1},
    )
    store = GroupSettingsStore(tmp_path / "policy.db", superusers={1})
    await store.initialize()
    await store.set_search_consent(1, True)
    await store.set_deployment_search_policy(
        DeploymentSearchPolicy(
            sources=(
                SearchSourceRule("page", "ordinary_meme", "page.example", kind="page"),
                SearchSourceRule("media", "ordinary_meme", "media.example", kind="image"),
            )
        )
    )
    for feature in ("ai", "meme", "online_meme"):
        await store.set_global_feature_enabled(feature, True)
        await store.set_feature_enabled(34, feature, True)
    features = FeatureService(settings, store)
    await features.initialize()

    class Classifier:
        async def classify(self, _text, *, budget=None):
            return SearchPurpose.ORDINARY_MEME

    policy = SearchPolicyService(features, classifier=Classifier())
    event = GroupMessageEvent(
        time=0,
        self_id=99,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        user_id=12,
        group_id=34,
        message=Message("/meme --mode online 猫猫表情"),
        raw_message="/meme --mode online 猫猫表情",
        font=0,
        sender={"user_id": 12, "role": "member"},
    )
    context = await policy.create_task_from_event(event)
    image = png_bytes()

    async def handle(_request):
        return httpx.Response(200, content=image, headers={"content-type": "image/png"})

    search = FakeSearch(
        [SearchHit("cat", "https://page.example/cat", "", "https://media.example/cat.png")]
    )
    service = OnlineMemeService(
        FakeLocalMemes(),
        policy,
        search,
        MemeImageClient(
            transport=httpx.MockTransport(handle),
            resolver=lambda *_: asyncio.sleep(0, result=["8.8.8.8"]),
        ),
    )
    ref = await service.search_and_download(context, "猫猫表情", "online", TaskBudget.start())
    assert isinstance(ref, TemporaryMemeRef)
    assert await service.read_for_send(ref.opaque_id, 34, 12, task_id=context.task_id)

    await store.set_deployment_search_policy(DeploymentSearchPolicy(sources=()))
    with pytest.raises(PermissionError):
        await service.read_for_send(ref.opaque_id, 34, 12, task_id=context.task_id)


async def never_resolve(*_):
    raise AssertionError("local-only meme lookup must not resolve or call out")
