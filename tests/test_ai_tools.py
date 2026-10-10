from types import SimpleNamespace

import httpx
import pytest

from aling.core.config import Settings
from aling.core.permissions import PermissionContext
from aling.core.storage import GroupSettingsStore
from aling.services.ai import ChatOutcome, ChatService
from aling.services.features import FeatureService
from aling.services.tools import ToolRegistry


async def test_tool_registry_is_explicit_read_only_allowlist(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        web_enabled=True,
        meme_enabled=True,
        allowed_groups={34},
    )
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)

    class FakeWeb:
        async def read(self, url, *, group_id, summarize=False, ai_enabled=False):
            return SimpleNamespace(title="Safe title", source="https://example.org", text="excerpt")

    class FakeMemes:
        async def search(self, query, *, limit=1):
            return [SimpleNamespace(resource_id="safe-id", description="shrug")]

    tools = ToolRegistry(features, FakeWeb(), FakeMemes())
    context = PermissionContext(12, 34, "member")
    await features.set(PermissionContext(12, 34, "admin"), "web", True)
    await features.set(PermissionContext(12, 34, "admin"), "meme", True)
    assert (await tools.call("web_read", {"url": "https://example.org"}, context))["ok"]
    result = await tools.call("meme_search", {"query": "shrug"}, context)
    assert result["resource_id"] == "safe-id"
    assert (await tools.call("system.exec", {"cmd": "whoami"}, context))["ok"] is False
    assert (await tools.call("group.mute", {}, context))["ok"] is False


async def test_tool_registry_rejects_extra_arguments_and_disabled_groups(tmp_path):
    settings = Settings(_env_file=None, onebot_access_token="test", web_enabled=True)
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)

    class Forbidden:
        async def read(self, *args, **kwargs):
            raise AssertionError("Invalid or disabled tool call must not run")

    tools = ToolRegistry(features, Forbidden(), Forbidden())
    context = PermissionContext(12, 34, "member")
    assert not (await tools.call("web_read", {"url": "https://example.org", "x": 1}, context))["ok"]
    assert not (await tools.call("meme_search", {"query": "shrug"}, context))["ok"]


async def test_tool_registry_returns_failure_not_success_on_timeout(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        web_enabled=True,
        allowed_groups={34},
    )
    store = GroupSettingsStore(tmp_path / "settings.db")
    await store.initialize()
    features = FeatureService(settings, store)
    await features.set(PermissionContext(12, 34, "admin"), "web", True)

    class SlowWeb:
        async def read(self, *args, **kwargs):
            import asyncio

            await asyncio.sleep(0.02)

    result = await ToolRegistry(features, SlowWeb(), object(), timeout=0.001).call(
        "web_read", {"url": "https://example.org"}, PermissionContext(12, 34, "member")
    )
    assert result == {"ok": False, "error": "操作超时。"}


def test_model_can_only_see_documented_tools():
    names = {tool["function"]["name"] for tool in ToolRegistry.definitions}
    assert names == {"web_read", "meme_search"}


async def test_ai_executes_at_most_two_validated_tools_and_never_system_calls():
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        ai_enabled=True,
        ai_api_key="fake",
        ai_model="test",
        ai_tools_enabled=True,
        ai_cooldown_seconds=0,
    )

    class FakeClient:
        async def chat_with_tools(self, prompt, definitions, history):
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {
                            "name": "web_read",
                            "arguments": '{"url":"https://example.org"}',
                        },
                    },
                    {
                        "id": "2",
                        "function": {"name": "meme_search", "arguments": '{"query":"shrug"}'},
                    },
                    {
                        "id": "3",
                        "function": {"name": "system_exec", "arguments": '{"cmd":"whoami"}'},
                    },
                ],
            }

        async def chat_followup(self, prompt, history, messages):
            assert [message["role"] for message in messages[1:]] == ["tool", "tool", "tool"]
            return {"content": "网页已读，也找到表情。"}

        async def chat(self, prompt, history):
            raise AssertionError("Supported provider must not fall back")

    class Registry:
        definitions = ToolRegistry.definitions

        def __init__(self):
            self.calls = []

        async def definitions_for(self, group_id):
            return self.definitions

        async def call(self, name, args, context):
            self.calls.append(name)
            return {"ok": True, "resource_id": "safe-meme" if name == "meme_search" else None}

    registry = Registry()
    service = ChatService(settings, FakeClient())
    outcome = await service.reply_with_tools(34, 12, "读网页再找图", registry)
    assert outcome == ChatOutcome("网页已读，也找到表情。", "safe-meme")
    assert registry.calls == ["web_read", "meme_search"]


async def test_unsupported_tool_provider_falls_back_to_single_turn_chat():
    settings = Settings(
        _env_file=None,
        onebot_access_token="test",
        ai_enabled=True,
        ai_api_key="fake",
        ai_model="test",
        ai_tools_enabled=True,
        ai_cooldown_seconds=0,
    )

    class UnsupportedClient:
        async def chat_with_tools(self, *args):
            request = httpx.Request("POST", "https://provider.invalid")
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError("unsupported tools", request=request, response=response)

        async def chat(self, prompt, history):
            return "普通聊天正常。"

    class Registry:
        definitions = ToolRegistry.definitions

        async def definitions_for(self, group_id):
            return self.definitions

        async def call(self, *args):
            raise AssertionError("Tools must not execute when the provider rejects them")

    outcome = await ChatService(settings, UnsupportedClient()).reply_with_tools(
        34, 12, "你好", Registry()
    )
    assert outcome == ChatOutcome("普通聊天正常。")


@pytest.mark.parametrize(
    "message",
    [
        None,
        [],
        {"tool_calls": [None]},
        {
            "tool_calls": [
                {"id": "1", "function": {"name": "web_read", "arguments": "{}"}},
                {"id": "2", "function": {"name": "meme_search", "arguments": "{}"}},
                None,
            ],
        },
    ],
)
async def test_malformed_tool_provider_response_fails_safely(message):
    options = Settings(
        _env_file=None,
        onebot_access_token="test",
        ai_enabled=True,
        ai_api_key="fake",
        ai_model="test",
        ai_tools_enabled=True,
    )

    class Client:
        async def chat_with_tools(self, *args):
            return message

    class Registry:
        definitions = ToolRegistry.definitions

        async def definitions_for(self, group_id):
            return self.definitions

        async def call(self, *args):
            raise AssertionError("Malformed tool calls must be rejected before execution")

    outcome = await ChatService(options, Client()).reply_with_tools(34, 12, "test", Registry())
    assert "暂时不可用" in outcome.text


async def test_model_tools_are_filtered_by_current_group_features(tmp_path):
    options = Settings(_env_file=None, onebot_access_token="test", web_enabled=True)
    store = GroupSettingsStore(tmp_path / "tools.db")
    await store.initialize()
    features = FeatureService(options, store)
    tools = ToolRegistry(features, object(), object())
    assert await tools.definitions_for(34) == []
    await features.set(PermissionContext(12, 34, "admin"), "web", True)
    assert [d["function"]["name"] for d in await tools.definitions_for(34)] == ["web_read"]


async def test_no_available_tools_uses_plain_chat_with_history():
    options = Settings(
        _env_file=None,
        onebot_access_token="test",
        ai_enabled=True,
        ai_api_key="fake",
        ai_model="test",
        ai_tools_enabled=True,
    )
    history = [{"role": "user", "content": "previous"}]

    class Client:
        async def chat(self, prompt, received_history):
            assert received_history == history
            return "plain chat"

    class Registry:
        async def definitions_for(self, group_id):
            return []

    assert await ChatService(options, Client()).reply_with_tools(
        34, 12, "test", Registry(), history=history
    ) == ChatOutcome("plain chat")


async def test_malformed_tool_followup_is_a_safe_failure():
    options = Settings(
        _env_file=None,
        onebot_access_token="test",
        ai_enabled=True,
        ai_api_key="fake",
        ai_model="test",
        ai_tools_enabled=True,
    )

    class Client:
        async def chat_with_tools(self, *args):
            return {
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {"name": "web_read", "arguments": "{}"},
                    }
                ]
            }

        async def chat_followup(self, *args):
            return None

    class Registry:
        async def definitions_for(self, group_id):
            return ToolRegistry.definitions

        async def call(self, *args):
            return {"ok": True, "excerpt": "real result"}

    result = await ChatService(options, Client()).reply_with_tools(34, 12, "test", Registry())
    assert "暂时不可用" in result.text
