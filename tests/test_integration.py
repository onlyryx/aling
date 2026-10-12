import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.log import logger
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from aling.app import build_app
from aling.core.config import Settings
from aling.core.logging import configure_logging
from aling.core.permissions import PermissionContext
from aling.core.storage import DeploymentSearchPolicy, SearchSourceRule
from aling.runtime import get_runtime
from aling.services.ai import ChatOutcome
from aling.services.search_budget import TaskBudget
from aling.services.search_policy import SearchPurpose


def event(text, *, role="member", mention=False, user=12, group=34):
    message = [{"type": "text", "data": {"text": text}}]
    if mention:
        message.insert(0, {"type": "at", "data": {"qq": "99"}})
    return {
        "time": 0,
        "self_id": 99,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": 1,
        "user_id": user,
        "group_id": group,
        "message": message,
        "raw_message": text,
        "font": 0,
        "sender": {"user_id": user, "role": role},
    }


class SearchClassifier:
    def __init__(self):
        self.calls = []

    async def classify(self, text, *, budget):
        self.calls.append(text)
        return SearchPurpose.TECHNICAL


def test_authenticated_onebot_commands_ai_permissions_and_ignored_events(tmp_path, monkeypatch):
    monkeypatch.setenv("ALING_EXAMPLE_ENABLED", "true")
    prompts = []
    model_messages = []

    def model(request):
        messages = json.loads(request.content)["messages"]
        model_messages.append(messages)
        prompt = messages[-1]["content"]
        prompts.append(prompt)
        if prompt == "error please":
            return httpx.Response(500, text="sensitive provider response")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "hello [CQ:at,qq=all]"}}]}
        )

    search_requests = []

    def search_backend(request):
        search_requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "http://search:8080/search"
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
                        "title": "unapproved private result",
                        "url": (
                            "https://private-user:url-credential-secret@attacker.invalid/secret"
                            "?token=query-url-secret"
                        ),
                        "content": "fake-body-secret",
                    },
                ]
            },
        )

    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    Image.new("RGB", (2, 2), "red").save(meme_dir / "hello.png")
    (meme_dir / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "id": "hello",
                    "description": "hello [CQ:at,qq=all]",
                    "tags": ["hello"],
                    "path": "hello.png",
                }
            ]
        ),
        encoding="utf-8",
    )
    config = Settings(
        _env_file=None,
        onebot_access_token="test-secret",
        database_path=tmp_path / "settings.db",
        superusers={12},
        webui_enabled=True,
        webui_admin_token="fictional-panel-key-" + "a" * 43,
        webui_owner_id=12,
        ai_api_key="fake-key",
        ai_model="test-model",
        ai_cooldown_seconds=0,
        allowed_groups={34},
        extra_plugins=["docs.plugins.example_plugin"],
        meme_dir=meme_dir,
    )
    app = build_app(
        config,
        ai_transport=httpx.MockTransport(model),
        search_transport=httpx.MockTransport(search_backend),
    )
    log_lines = []
    configure_logging("DEBUG", sink=log_lines.append)
    with TestClient(app, base_url=config.webui_origin) as client:
        runtime = get_runtime()
        expiry_task = runtime._session_expiry_task
        assert expiry_task is not None and not expiry_task.done()
        assert client.get("/webui/").status_code == 200
        assert (
            client.get("/api/admin/session", headers={"Host": "127.0.0.1:8080"}).status_code == 401
        )
        assert app.state.panel_runtime is runtime
        assert client.get("/health", headers={"Host": "bot:8080"}).json() == {"status": "ok"}
        assert client.get("/ready").status_code == 503
        login = client.post(
            "/api/admin/session",
            json={"token": config.webui_admin_token.get_secret_value()},
            headers={"Origin": config.webui_origin},
        )
        assert login.status_code == 200
        client.headers.update(
            {"Origin": config.webui_origin, "X-CSRF-Token": login.json()["csrf_token"]}
        )

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/onebot/v11/ws", headers={"X-Self-ID": "99"}):
                pass
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/onebot/v11/ws", headers={"X-Self-ID": "99", "Authorization": "Bearer wrong"}
            ):
                pass
        headers = {"X-Self-ID": "99", "Authorization": "Bearer test-secret", "Host": "bot:8080"}
        with client.websocket_connect("/onebot/v11/ws", headers=headers) as ws:
            assert client.get("/ready").json() == {"status": "ready"}
            # A subsequent /ping proves these preceding messages produced no reply.
            private_discussion = event("fictional private DM body")
            private_discussion.update(message_type="private", sub_type="friend")
            private_discussion.pop("group_id")
            assert private_discussion["message_type"] == "private"
            for ignored in [
                private_discussion,
                event("阿零 hello"),
                event("hello", mention=True, user=99),
                event("hello", mention=True, group=56),
            ]:
                ws.send_json(ignored)

            def exchange(payload):
                ws.send_json(payload)
                request = ws.receive_json()
                assert request["action"] == "send_msg"
                if payload["message_type"] == "group":
                    assert request["params"]["group_id"] == 34
                else:
                    assert request["params"]["user_id"] == payload["user_id"]
                ws.send_json(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"message_id": 2},
                        "echo": request["echo"],
                    }
                )
                return request["params"]["message"]

            assert exchange(event("/ping"))[0]["data"]["text"] == "pong"
            assert (
                "超级用户" in exchange(event("/status", role="owner", user=13))[0]["data"]["text"]
            )
            ws.send_json(event("/status"))
            status_request = ws.receive_json()
            assert status_request["action"] == "get_status"
            ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"online": False, "good": True},
                    "echo": status_request["echo"],
                }
            )
            status_reply = ws.receive_json()
            assert "QQ 在线：否" in status_reply["params"]["message"][0]["data"]["text"]
            ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 2},
                    "echo": status_reply["echo"],
                }
            )
            mute = event("/mute", role="admin")
            mute["message"] = [
                {"type": "text", "data": {"text": "/mute "}},
                {"type": "at", "data": {"qq": "23"}},
            ]
            ws.send_json(mute)
            for expected_action, data in [
                ("get_group_member_info", {"role": "admin"}),
                ("get_group_member_info", {"role": "member"}),
                ("set_group_ban", {}),
                ("send_msg", {"message_id": 2}),
            ]:
                request = ws.receive_json()
                assert request["action"] == expected_action
                if expected_action == "set_group_ban":
                    assert request["params"] == {
                        "group_id": 34,
                        "user_id": 23,
                        "duration": 600,
                    }
                elif expected_action == "send_msg":
                    assert request["params"]["message"][0]["data"]["text"] == (
                        "已禁言成员 23，时长 10 分钟。"
                    )
                ws.send_json(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": data,
                        "echo": request["echo"],
                    }
                )
            help_text = exchange(event("/help"))[0]["data"]["text"]
            assert "/help" in help_text and "阿零（ALING）" in help_text
            denied = exchange(event("/proactive on", user=13))[0]["data"]["text"]
            assert "管理员" in denied
            assert "开启" in exchange(event("/proactive on"))[0]["data"]["text"]
            assert "开启" in exchange(event("/proactive on", role="admin"))[0]["data"]["text"]
            assert "关闭" in exchange(event("/proactive off", role="admin"))[0]["data"]["text"]
            denied_global = exchange(event("/feature global web off", role="owner", user=13))[0][
                "data"
            ]["text"]
            assert "超级用户" in denied_global
            global_off = exchange(event("/feature global web off", user=12))[0]["data"]["text"]
            assert "全局" in global_off and "关闭" in global_off
            blocked_group_on = exchange(event("/feature web on", role="admin"))[0]["data"]["text"]
            assert "超级用户" in blocked_group_on
            private_global = event("/feature global web on", user=12)
            private_global.update(message_type="private", sub_type="friend")
            private_global.pop("group_id")
            global_on = exchange(private_global)[0]["data"]["text"]
            assert "全局" in global_on and "开启" in global_on
            assert "开启" in exchange(event("/feature web on", role="admin"))[0]["data"]["text"]
            exchange(event("/feature global ai on"))
            assert "开启" in exchange(event("/feature ai on", role="admin"))[0]["data"]["text"]
            assert "示例插件已加载" in exchange(event("/example", role="admin"))[0]["data"]["text"]
            exchange(event("/feature global meme on"))
            assert "开启" in exchange(event("/feature meme on", role="admin"))[0]["data"]["text"]
            for feature in ("search", "online_meme"):
                denied = exchange(event(f"/feature global {feature} on", user=13, role="owner"))[0][
                    "data"
                ]["text"]
                assert "超级用户" in denied
                blocked = exchange(event(f"/feature global {feature} on"))[0]["data"]["text"]
                assert "acknowledgement" in blocked
            import asyncio

            asyncio.run(runtime.store.set_search_consent(12, True))
            for feature in ("search", "online_meme"):
                assert "开启" in exchange(event(f"/feature global {feature} on"))[0]["data"]["text"]
                denied = exchange(event(f"/feature {feature} on", user=13))[0]["data"]["text"]
                assert "管理员" in denied
                assert (
                    "开启"
                    in exchange(event(f"/feature {feature} on", role="admin"))[0]["data"]["text"]
                )
                assert (
                    "关闭" in exchange(event(f"/feature global {feature} off"))[0]["data"]["text"]
                )
                assert (
                    "关闭"
                    in exchange(event(f"/feature {feature} off", role="admin"))[0]["data"]["text"]
                )
            classifier = SearchClassifier()
            runtime.search_policy.classifier = classifier
            download_calls = []
            from aling.plugins import meme as meme_plugin

            monkeypatch.setattr(meme_plugin._limiter, "cooldown", 0)

            async def fake_download(*args, **kwargs):
                download_calls.append((args, kwargs))
                raise AssertionError("disabled online meme must not download")

            runtime.online_memes.image_client.fetch = fake_download
            assert "联网搜索当前未启用" in exchange(event("/help"))[0]["data"]["text"]
            disabled_search_reply = exchange(event("/search disabled-query-secret"))[0]["data"][
                "text"
            ]
            assert "Python asyncio" not in disabled_search_reply
            exchange(event("/meme --mode online disabled-query-secret"))
            assert classifier.calls == []
            assert search_requests == []
            assert download_calls == []
            assert client.post("/api/admin/search-probe", json={"group_id": 34}).status_code == 409

            query = "disabled-ai-tool-secret"
            task_context = client.portal.call(
                runtime.search_policy.create_task_from_event,
                GroupMessageEvent.model_validate(event(query)),
            )
            client.portal.call(runtime.features.set_ai_tools_enabled, 12, True)
            permission = PermissionContext(12, 34, "member")

            async def call_tool(name, arguments, task_budget):
                return await runtime.tools.call(
                    name,
                    arguments,
                    permission,
                    task_context=task_context,
                    budget=task_budget,
                    prompt=query,
                )

            search_tool = client.portal.call(
                call_tool,
                "web_search",
                {"query_id": task_context.query_id},
                TaskBudget.start(),
            )
            meme_tool = client.portal.call(
                call_tool,
                "meme_search",
                {"query": query, "mode": "online"},
                TaskBudget.start(),
            )
            assert search_tool["ok"] is False
            assert meme_tool["error"] == "online_memes_not_ready"
            assert classifier.calls == []
            assert search_requests == []
            assert download_calls == []
            client.portal.call(runtime.features.set_ai_tools_enabled, 12, False)
            asyncio.run(
                runtime.store.set_deployment_search_policy(
                    DeploymentSearchPolicy(
                        sources=(SearchSourceRule("python", "technical", "docs.python.org", "/3"),)
                    )
                )
            )
            asyncio.run(runtime.features.set_global(12, "search", True))
            asyncio.run(runtime.store.set_feature_enabled(34, "search", True))
            runtime.search_policy.classifier = classifier
            query_secret = "fake-query-privacy-731"
            search_reply = exchange(event(f"/search {query_secret}"))[0]["data"]["text"]
            assert "Python asyncio" in search_reply
            assert "https://docs.python.org/3/library/asyncio.html" in search_reply
            assert "Official documentation" in search_reply
            assert classifier.calls == [query_secret]
            assert len(search_requests) == 1
            assert not any(query_secret in str(line) for line in log_lines)
            enabled_help = exchange(event("/help"))[0]["data"]["text"]
            assert "联网搜索当前已启用" in enabled_help
            assert "在线表情当前未启用" in enabled_help
            assert "--mode local|auto|online" in enabled_help
            meme = exchange(event("/meme hello"))
            assert classifier.calls == [query_secret]
            assert [segment["type"] for segment in meme] == ["text", "image"]
            assert "[CQ:at,qq=all]" in meme[0]["data"]["text"]
            assert meme[1]["data"]["file"].startswith("base64://")
            ws.send_json(event("/meme hello"))
            image_request = ws.receive_json()
            assert image_request["params"]["message"][1]["type"] == "image"
            ws.send_json(
                {
                    "status": "failed",
                    "retcode": 100,
                    "data": None,
                    "message": "private adapter error",
                    "echo": image_request["echo"],
                }
            )
            fallback = ws.receive_json()
            assert "图片发送失败" in fallback["params"]["message"][0]["data"]["text"]
            ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 2},
                    "echo": fallback["echo"],
                }
            )
            assert exchange(event("/ping", mention=True))[0]["data"]["text"] == "pong"
            reply = exchange(event("my question", mention=True))
            assert classifier.calls == [query_secret]
            assert reply == [{"type": "text", "data": {"text": "hello [CQ:at,qq=all]"}}]
            assert "暂时" in exchange(event("error please", mention=True))[0]["data"]["text"]
            assert prompts == ["my question", "error please"]
            exchange(event("/feature global memory on"))
            warning = exchange(event("/feature memory on", role="admin"))[0]["data"]["text"]
            assert "发送给模型服务" in warning
            assert (
                "开启"
                in exchange(event("/feature memory confirm", role="admin"))[0]["data"]["text"]
            )
            exchange(event("remember this", mention=True))
            assert (
                "已开启" in exchange(event("/feature memory on", role="admin"))[0]["data"]["text"]
            )
            exchange(event("what did I ask?", mention=True))
            assert [message["content"] for message in model_messages[-1]] == [
                config.ai_system_prompt,
                "本次请求没有启用可调用工具；不要声称已联网搜索或读取网页。",
                "remember this",
                "hello [CQ:at,qq=all]",
                "what did I ask?",
            ]
            assert "已清除" in exchange(event("/forget"))[0]["data"]["text"]
            exchange(event("/feature memory off", role="admin"))
            exchange(event("/feature memory on", role="admin"))
            exchange(event("/feature ai off", role="admin"))
            assert (
                "确认已过期"
                in exchange(event("/feature memory confirm", role="admin"))[0]["data"]["text"]
            )
            exchange(event("/feature ai on", role="admin"))
            exchange(event("/feature memory on", role="admin"))
            exchange(event("/feature memory confirm", role="admin"))
            exchange(event("fresh question", mention=True))
            assert [message["role"] for message in model_messages[-1]] == [
                "system",
                "system",
                "user",
            ]
            assert "关闭" in exchange(event("/feature global memory off"))[0]["data"]["text"]
            assert "已清除" in exchange(event("/forget"))[0]["data"]["text"]
            exchange(event("/feature global memory on"))
            exchange(event("after global disable", mention=True))
            assert len(model_messages[-1]) == 3
            assert "/help" in exchange(event("/unknown-command"))[0]["data"]["text"]

            async def selected_meme(*args, **kwargs):
                return ChatOutcome("AI text survives an image failure", "hello")

            monkeypatch.setattr(runtime.chat, "reply_with_tools", selected_meme)
            asyncio.run(runtime.features.set_ai_tools_enabled(12, True))
            ws.send_json(event("send a meme", mention=True))
            image_request = ws.receive_json()
            assert image_request["params"]["message"][1]["type"] == "image"
            ws.send_json(
                {
                    "status": "failed",
                    "retcode": 100,
                    "data": None,
                    "echo": image_request["echo"],
                }
            )
            fallback = ws.receive_json()
            assert "AI text survives" in fallback["params"]["message"][0]["data"]["text"]
            ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 2},
                    "echo": fallback["echo"],
                }
            )
        assert client.get("/ready").status_code == 503
        panel_overview = client.get("/api/admin/overview")
        assert panel_overview.status_code == 200
    assert expiry_task.done() and runtime._session_expiry_task is None
    assert not runtime.sessions._sessions
    bot_logs = "".join(map(str, log_lines))
    assert "fictional private DM body" not in panel_overview.text
    assert "fictional private DM body" not in bot_logs
    assert "url-credential-secret" not in bot_logs
    assert "query-url-secret" not in bot_logs
    assert "fake-body-secret" not in bot_logs
    assert str(meme_dir / "hello.png") not in bot_logs


def test_logging_omits_framework_message_content_and_exception_locals():
    lines = []
    configure_logging("DEBUG", sink=lines.append)
    logger.bind().opt(exception=ValueError("SECRET")).warning("third party SECRET")
    logging.getLogger("httpx").warning("https://secret-host/?key=SECRET")
    logger.bind(aling_event=True).info("service_ready")
    output = "".join(str(line) for line in lines)
    assert "SECRET" not in output
    assert "secret-host" not in output
    assert "service_ready" in output
