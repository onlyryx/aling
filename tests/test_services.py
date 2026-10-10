import asyncio
import gzip
import json

import httpx
import pytest

from aling.core.config import Settings
from aling.core.permissions import PermissionContext
from aling.core.storage import GroupSettingsStore
from aling.services.ai import ChatService, OpenAIClient
from aling.services.proactive import ProactiveSender, send_group_text
from aling.services.web import SafeWebClient, UnsafeURL


def settings(**kwargs):
    return Settings(_env_file=None, onebot_access_token="test", **kwargs)


async def test_ai_sends_only_current_prompt_and_bounds_text_output():
    requests = []

    def endpoint(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "abcdefghij"}}]})

    config = settings(ai_api_key="fake", ai_model="test-model", ai_max_output_chars=5)
    client = OpenAIClient(config, transport=httpx.MockTransport(endpoint))
    try:
        service = ChatService(config, client)
        assert await service.reply(34, "first question") == "abcde"
        assert "稍后" in await service.reply(34, "second question")
        assert len(requests) == 1
        payload = json.loads(requests[0].content)
        assert str(requests[0].url) == "https://api.openai.com/v1/chat/completions"
        assert requests[0].headers["authorization"] == "Bearer fake"
        assert payload["messages"][1] == {"role": "user", "content": "first question"}
        assert len(payload["messages"]) == 2
        assert "tools" not in payload
        assert requests[0].extensions["timeout"]["read"] == 30
    finally:
        await client.aclose()


@pytest.mark.parametrize("failure", ["http", "timeout", "invalid", "empty", "large"])
async def test_ai_provider_failures_return_safe_user_message(failure):
    def endpoint(request):
        if failure == "http":
            return httpx.Response(401, text="SECRET response body")
        if failure == "timeout":
            raise httpx.ReadTimeout("SECRET url and prompt", request=request)
        if failure == "invalid":
            return httpx.Response(200, json={"choices": []})
        if failure == "empty":
            return httpx.Response(200, json={"choices": [{"message": {"content": " "}}]})
        return httpx.Response(200, content=b"x" * 1_000_001)

    config = settings(ai_api_key="fake", ai_model="test")
    client = OpenAIClient(config, transport=httpx.MockTransport(endpoint))
    try:
        answer = await ChatService(config, client).reply(34, "private prompt")
        assert "暂时" in answer
        assert "SECRET" not in answer and "private prompt" not in answer
    finally:
        await client.aclose()


async def test_disabled_oversize_out_of_scope_ai_never_calls_provider():
    def forbidden(request):
        pytest.fail("Provider must not be called")

    config = settings(allowed_groups={34})
    client = OpenAIClient(config, transport=httpx.MockTransport(forbidden))
    try:
        assert "未启用" in await ChatService(config, client).reply(34, "hello")
        config.ai_api_key = "fake-key"
        config.ai_model = "test-model"
        service = ChatService(config, client)
        assert "过长" in await service.reply(34, "x" * 2001)
        assert "未开放" in await service.reply(56, "hello")
    finally:
        await client.aclose()


async def test_ai_concurrency_and_total_deadline():
    async def slow_endpoint(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "reply"}}]})

    config = settings(ai_api_key="fake", ai_model="test", ai_timeout=0.02, ai_max_concurrency=1)
    client = OpenAIClient(config, transport=httpx.MockTransport(slow_endpoint))
    try:
        service = ChatService(config, client)
        first = asyncio.create_task(service.reply(34, "hello"))
        await asyncio.sleep(0)
        assert "繁忙" in await service.reply(56, "hello")
        assert "暂时" in await first
        assert "暂时" in await service.reply(78, "released slot")
    finally:
        await client.aclose()


async def test_ai_rejects_raw_compressed_response_without_decoding():
    iterated = []

    class CompressedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            iterated.append(True)
            yield gzip.compress(b"x" * 2_000_000)

    def endpoint(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=CompressedBody())

    config = settings(ai_api_key="fake", ai_model="test")
    client = OpenAIClient(config, transport=httpx.MockTransport(endpoint))
    try:
        with pytest.raises(ValueError, match="Compressed"):
            await client.chat("hello")
        assert not iterated
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://169.254.169.254",
        "http://[::1]",
        "http://[::ffff:127.0.0.1]",
        "http://[ff02::1]",
        "http://224.0.0.1",
        "http://user:password@example.org",
        "https://example.org:8443",
        "http://localhost",
        "http://2130706433",
        "http://0x7f000001",
        "http://example.org\\@127.0.0.1",
    ],
)
async def test_web_rejects_unsafe_destinations_before_request(url):
    async def resolver(host, port):
        return ["127.0.0.1"]

    def forbidden(request):
        pytest.fail("Unsafe destination was requested")

    client = SafeWebClient(transport=httpx.MockTransport(forbidden), resolver=resolver)
    try:
        with pytest.raises(UnsafeURL):
            await client.fetch(url)
    finally:
        await client.aclose()


async def test_web_pins_public_dns_address_and_keeps_tls_hostname():
    requests = []
    resolutions = []

    async def resolver(host, port):
        resolutions.append((host, port))
        return ["93.184.216.34"] if len(resolutions) == 1 else ["127.0.0.1"]

    def endpoint(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<h1>hello</h1>")

    client = SafeWebClient(transport=httpx.MockTransport(endpoint), resolver=resolver)
    try:
        assert "hello" in await client.fetch("https://example.org/page")
        assert requests[0].url.host == "93.184.216.34"
        assert requests[0].headers["host"] == "example.org"
        assert requests[0].extensions["sni_hostname"] == "example.org"
        assert requests[0].extensions["timeout"]["connect"] == 10
        with pytest.raises(UnsafeURL):
            await client.fetch("https://example.org/page")
        assert len(requests) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize("case", ["mixed-dns", "redirect", "too-large", "wrong-type"])
async def test_web_rejects_mixed_dns_redirect_and_unbounded_content(case):
    async def resolver(host, port):
        return ["93.184.216.34", "10.0.0.1"] if case == "mixed-dns" else ["93.184.216.34"]

    def endpoint(request):
        if case == "mixed-dns":
            pytest.fail("Mixed public/private DNS must not connect")
        if case == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1"})
        if case == "too-large":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 11)
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"x"
        )

    client = SafeWebClient(max_bytes=10, transport=httpx.MockTransport(endpoint), resolver=resolver)
    try:
        with pytest.raises((UnsafeURL, ValueError)):
            await client.fetch("https://example.org")
    finally:
        await client.aclose()


async def test_proactive_sender_requires_permission_opt_in_and_cooldown(tmp_path):
    sent = []

    async def send(group, text):
        sent.append((group, text))

    config = settings(allowed_groups={34})
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    sender = ProactiveSender(config, store)
    with pytest.raises(PermissionError):
        await sender.set_enabled(PermissionContext(12, 34, "member"), True)
    await sender.set_enabled(PermissionContext(12, 34, "admin"), True)
    assert not await sender.send(34, "hello", send)
    config.proactive_enabled = True
    assert await sender.send(34, "hello", send)
    assert not await sender.send(34, "again", send)
    assert not await sender.send(56, "outside scope", send)
    await sender.set_enabled(PermissionContext(12, 34, "admin"), False)
    assert not await sender.send(34, "disabled", send)
    assert sent == [(34, "hello")]


async def test_scheduled_text_is_sent_as_plain_text_not_cq_code():
    class FakeBot:
        async def call_api(self, action, **kwargs):
            assert action == "send_group_msg"
            assert kwargs["group_id"] == 34
            assert len(kwargs["message"]) == 1
            assert kwargs["message"][0].type == "text"
            assert kwargs["message"][0].data["text"] == "hello [CQ:at,qq=all]"

    await send_group_text(FakeBot(), 34, "hello [CQ:at,qq=all]")


async def test_web_rejects_compression_before_decoding_and_parser_omits_scripts():
    async def resolver(host, port):
        return ["93.184.216.34"]

    def endpoint(request):
        if request.url.path == "/compressed":
            return httpx.Response(
                200, headers={"content-type": "text/html", "content-encoding": "gzip"}, content=b""
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>Article</title><script>secret script</script><h1>Useful text</h1>",
        )

    client = SafeWebClient(transport=httpx.MockTransport(endpoint), resolver=resolver)
    try:
        with pytest.raises(ValueError, match="Compressed"):
            await client.fetch("https://example.org/compressed")
        page = await client.parse("https://example.org/article")
        assert page.title == "Article"
        assert "Useful text" in page.text and "secret script" not in page.text
    finally:
        await client.aclose()
