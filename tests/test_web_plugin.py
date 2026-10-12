import httpx

from aling.core.config import Settings
from aling.services.web import SafeWebClient, WebReadService


def settings(**kwargs):
    return Settings(_env_file=None, onebot_access_token="test", **kwargs)


async def test_web_read_prefers_article_and_strips_untrusted_noncontent():
    async def resolver(host, port):
        return ["93.184.216.34"]

    def endpoint(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="""<title>Example</title><nav>nav text</nav><main><article>Useful body
            <script>secret instruction</script></article></main><footer>footer text</footer>""",
        )

    client = SafeWebClient(transport=httpx.MockTransport(endpoint), resolver=resolver)
    try:
        result = await WebReadService(settings(), client, chat=None).read(
            "https://example.org/a?token=private", group_id=34
        )
    finally:
        await client.aclose()
    assert result.title == "Example"
    assert result.source == "https://example.org/a"
    assert "Useful body" in result.text
    assert "nav text" not in result.text and "secret instruction" not in result.text
    assert "private" not in result.source


async def test_web_summary_is_optional_falls_back_and_marks_model_usage():
    async def resolver(host, port):
        return ["93.184.216.34"]

    def endpoint(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>Example</title><article>Source body</article>",
        )

    client = SafeWebClient(transport=httpx.MockTransport(endpoint), resolver=resolver)

    class FakeChat:
        calls = []

        async def reply(self, group_id, prompt):
            self.calls.append((group_id, prompt))
            return "AI 服务暂时不可用，请稍后再试。"

    chat = FakeChat()
    try:
        result = await WebReadService(
            settings(ai_api_key="fake", ai_model="test"), client, chat
        ).read("https://example.org", group_id=34, summarize=True, ai_enabled=True)
    finally:
        await client.aclose()
    assert len(chat.calls) == 1
    assert "Source body" in chat.calls[0][1]
    assert "不可信" in chat.calls[0][1]
    assert result.used_summary is False
    assert "Source body" in result.text


async def test_web_summary_disabled_never_calls_ai():
    async def resolver(host, port):
        return ["93.184.216.34"]

    client = SafeWebClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/html"}, text="<main>visible excerpt</main>"
            )
        ),
        resolver=resolver,
    )

    class ForbiddenChat:
        async def reply(self, group_id, prompt):
            raise AssertionError("AI disabled: must not call model")

    try:
        result = await WebReadService(settings(), client, ForbiddenChat()).read(
            "https://example.org", group_id=34, summarize=True
        )
    finally:
        await client.aclose()
    assert "visible excerpt" in result.text
    assert result.used_summary is False


async def test_unknown_page_charset_falls_back_to_utf8():
    async def resolver(host, port):
        return ["93.184.216.34"]

    client = SafeWebClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html; charset=invalid-charset"},
                content="<main>可读正文</main>".encode(),
            )
        ),
        resolver=resolver,
    )
    try:
        assert "可读正文" in (await client.parse("https://example.org")).text
    finally:
        await client.aclose()


async def test_explicit_url_keeps_web_gate_without_search_sources_classifier_or_ai(tmp_path):
    from aling.core.permissions import PermissionContext
    from aling.core.storage import GroupSettingsStore
    from aling.services.features import FeatureService
    from aling.services.tools import ToolRegistry

    config = settings(superusers={12}, allowed_groups={34})
    store = GroupSettingsStore(tmp_path / "explicit-read.db", superusers={12})
    await store.initialize()
    features = FeatureService(config, store)
    await features.initialize()
    await features.set_ai_tools_enabled(12, True)
    await features.set_global(12, "web", True)
    await store.set_feature_enabled(34, "web", True)
    calls = []

    async def resolver(host, port):
        assert host == "unlisted.example.org" and port == 443
        return ["93.184.216.34"]

    def endpoint(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<article>Explicit body</article>"
        )

    client = SafeWebClient(transport=httpx.MockTransport(endpoint), resolver=resolver)
    tools = ToolRegistry(features, WebReadService(config, client, chat=None), memes=None)
    try:
        context = PermissionContext(12, 34, "member")
        assert not await features.enabled(34, "ai")
        assert not await features.enabled(34, "search")
        assert not (await store.get_deployment_search_policy()).sources
        url = "https://unlisted.example.org/path"
        result = await tools.call(
            "web_read", {"url": url}, context, task_context=object(), prompt=url
        )
        assert result["ok"] and "Explicit body" in result["excerpt"]
        await store.set_feature_enabled(34, "web", False)
        assert not (
            await tools.call("web_read", {"url": "https://unlisted.example.org/path"}, context)
        )["ok"]
        assert len(calls) == 1
    finally:
        await client.aclose()
