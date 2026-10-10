import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from nonebot.log import logger
from starlette.websockets import WebSocketDisconnect

from aling.app import build_app
from aling.core.config import Settings
from aling.core.logging import configure_logging


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


def test_authenticated_onebot_commands_ai_permissions_and_ignored_events(tmp_path):
    prompts = []

    def model(request):
        prompt = json.loads(request.content)["messages"][1]["content"]
        prompts.append(prompt)
        if prompt == "error please":
            return httpx.Response(500, text="sensitive provider response")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "hello [CQ:at,qq=all]"}}]}
        )

    config = Settings(
        _env_file=None,
        onebot_access_token="test-secret",
        database_path=tmp_path / "settings.db",
        ai_enabled=True,
        ai_api_key="fake-key",
        ai_model="test-model",
        ai_cooldown_seconds=0,
        allowed_groups={34},
    )
    app = build_app(config, ai_transport=httpx.MockTransport(model))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/onebot/v11/ws", headers={"X-Self-ID": "99"}):
                pass
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/onebot/v11/ws", headers={"X-Self-ID": "99", "Authorization": "Bearer wrong"}
            ):
                pass
        headers = {"X-Self-ID": "99", "Authorization": "Bearer test-secret"}
        with client.websocket_connect("/onebot/v11/ws", headers=headers) as ws:
            # A subsequent /ping proves these preceding messages produced no reply.
            for ignored in [
                event("ordinary private discussion"),
                event("阿零 hello"),
                event("hello", mention=True, user=99),
                event("hello", mention=True, group=56),
            ]:
                ws.send_json(ignored)

            def exchange(payload):
                ws.send_json(payload)
                request = ws.receive_json()
                assert request["action"] == "send_msg"
                assert request["params"]["group_id"] == 34
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
            help_text = exchange(event("/help"))[0]["data"]["text"]
            assert "/help" in help_text and "阿零（ALING）" in help_text
            denied = exchange(event("/proactive on"))[0]["data"]["text"]
            assert "管理员" in denied
            assert "开启" in exchange(event("/proactive on", role="admin"))[0]["data"]["text"]
            assert "关闭" in exchange(event("/proactive off", role="admin"))[0]["data"]["text"]
            assert exchange(event("/ping", mention=True))[0]["data"]["text"] == "pong"
            reply = exchange(event("my question", mention=True))
            assert reply == [{"type": "text", "data": {"text": "hello [CQ:at,qq=all]"}}]
            assert "暂时" in exchange(event("error please", mention=True))[0]["data"]["text"]
            assert prompts == ["my question", "error please"]


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
