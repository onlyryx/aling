import asyncio
import json
from dataclasses import dataclass

import httpx

from aling.core.config import Settings
from aling.core.logging import log
from aling.core.permissions import PermissionContext
from aling.core.rate_limit import RateLimiter
from aling.services.http import read_limited


class OpenAIClient:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ai_timeout),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
            limits=httpx.Limits(max_connections=settings.ai_max_concurrency),
        )

    async def chat(self, prompt: str, history: list[dict[str, str]] | None = None) -> str:
        config = self.settings
        payload = self._payload(prompt, history)
        data = await self._request(payload)
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty or invalid model content")
        return content.strip()[: config.ai_max_output_chars]

    async def chat_with_tools(
        self,
        prompt: str,
        tools: list[dict],
        history: list[dict[str, str]] | None = None,
        extra_messages: list[dict] | None = None,
    ) -> dict:
        payload = self._payload(prompt, history)
        payload["messages"].extend(extra_messages or [])
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        data = await self._request(payload)
        return data["choices"][0]["message"]

    async def chat_followup(
        self,
        prompt: str,
        history: list[dict[str, str]] | None,
        extra_messages: list[dict],
    ) -> dict:
        payload = self._payload(prompt, history)
        payload["messages"].extend(extra_messages)
        data = await self._request(payload)
        return data["choices"][0]["message"]

    def _payload(self, prompt: str, history: list[dict[str, str]] | None) -> dict:
        messages = [{"role": "system", "content": self.settings.ai_system_prompt}]
        if history:
            messages.extend(history[-12:])
        messages.append({"role": "user", "content": prompt})
        return {
            "model": self.settings.ai_model,
            "messages": messages,
            "max_tokens": self.settings.ai_max_tokens,
            "stream": False,
        }

    async def _request(self, payload: dict) -> dict:
        config = self.settings
        async with asyncio.timeout(config.ai_timeout):
            async with self._client.stream(
                "POST",
                config.ai_base_url + "/chat/completions",
                headers={
                    "Authorization": "Bearer " + config.ai_api_key.get_secret_value(),
                    "Accept-Encoding": "identity",
                },
                json=payload,
            ) as response:
                response.raise_for_status()
                return json.loads(await read_limited(response, 1_000_000))

    async def aclose(self) -> None:
        await self._client.aclose()


class ChatService:
    def __init__(self, settings: Settings, client: OpenAIClient) -> None:
        self.settings, self.client = settings, client
        self.limiter = RateLimiter(settings.ai_cooldown_seconds, settings.ai_max_per_hour)
        self._active = 0

    async def reply(
        self, group_id: int, prompt: str, *, history: list[dict[str, str]] | None = None
    ) -> str:
        config = self.settings
        if not config.group_allowed(group_id):
            return "本群未开放 AI 聊天。"
        if not config.ai_enabled:
            return "AI 聊天未启用，请联系机器人维护者配置。"
        prompt = prompt.strip()
        if not prompt:
            return "@ 我时请带上想问的问题。"
        if len(prompt) > config.ai_max_input_chars:
            return "提问过长，请缩短后再试。"
        if self._active >= config.ai_max_concurrency:
            return "AI 当前繁忙，请稍后再试。"
        if not self.limiter.allow(group_id):
            return "本群 AI 调用过于频繁，请稍后再试。"
        self._active += 1
        try:
            return await self.client.chat(prompt, history)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as exc:
            # Never log exception text: providers may echo URLs, credentials or prompts.
            log.warning("AI request failed ({})", type(exc).__name__)
            return "AI 服务暂时不可用，请稍后再试。"
        finally:
            self._active -= 1

    async def reply_with_tools(
        self,
        group_id: int,
        user_id: int,
        prompt: str,
        registry,
        *,
        history: list[dict[str, str]] | None = None,
    ) -> "ChatOutcome":
        config = self.settings
        if not config.group_allowed(group_id) or not config.ai_enabled:
            return ChatOutcome(await self.reply(group_id, prompt))
        if not config.ai_tools_enabled:
            return ChatOutcome(await self.reply(group_id, prompt))
        prompt = prompt.strip()
        if not prompt or len(prompt) > config.ai_max_input_chars:
            return ChatOutcome("提问内容无效或过长，请调整后再试。")
        if self._active >= config.ai_max_concurrency:
            return ChatOutcome("AI 当前繁忙，请稍后再试。")
        if not self.limiter.allow(group_id):
            return ChatOutcome("本群 AI 调用过于频繁，请稍后再试。")
        self._active += 1
        try:
            async with asyncio.timeout(config.ai_tool_turn_timeout):
                definitions = await registry.definitions_for(group_id)
                if not definitions:
                    return ChatOutcome(await self.client.chat(prompt, history))
                try:
                    message = await self.client.chat_with_tools(prompt, definitions, history)
                except httpx.HTTPStatusError:
                    return ChatOutcome(await self.client.chat(prompt, history))
                if not isinstance(message, dict):
                    raise ValueError("Invalid model message")
                calls = message.get("tool_calls")
                if calls is not None and not isinstance(calls, list):
                    raise ValueError("Invalid model tool calls")
                if not isinstance(calls, list) or not calls:
                    return ChatOutcome(_bounded_content(message, config.ai_max_output_chars))
                for call in calls:
                    if (
                        not isinstance(call, dict)
                        or not isinstance(call.get("id"), str)
                        or not call["id"]
                        or not isinstance(call.get("function"), dict)
                        or not isinstance(call["function"].get("name"), str)
                        or not isinstance(call["function"].get("arguments"), str)
                    ):
                        raise ValueError("Invalid model tool call")
                calls = calls[:2]
                tool_messages = [message]
                resources = []
                for call in calls:
                    function = call.get("function", {}) if isinstance(call, dict) else {}
                    name = function.get("name") if isinstance(function, dict) else None
                    raw_arguments = (
                        function.get("arguments") if isinstance(function, dict) else None
                    )
                    try:
                        arguments = json.loads(raw_arguments)
                    except (TypeError, json.JSONDecodeError):
                        arguments = None
                    result = await registry.call(
                        name, arguments, PermissionContext(user_id, group_id, "member")
                    )
                    if result.get("ok") is not True:
                        return ChatOutcome("工具请求未能完成，请核对功能开关或稍后再试。")
                    if name == "meme_search" and result.get("resource_id"):
                        resources.append(result["resource_id"])
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(call.get("id", ""))[:100],
                            "content": json.dumps(result, ensure_ascii=False)[:2000],
                        }
                    )
                for call in message["tool_calls"][2:]:
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(call.get("id", ""))[:100],
                            "content": json.dumps(
                                {"ok": False, "error": "本轮工具调用数量已达上限。"},
                                ensure_ascii=False,
                            ),
                        }
                    )
                followup = await self.client.chat_followup(prompt, history, tool_messages)
                return ChatOutcome(
                    _bounded_content(followup, config.ai_max_output_chars),
                    resources[0] if resources else None,
                )
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            log.warning("AI tool turn failed ({})", type(exc).__name__)
            return ChatOutcome("工具或 AI 服务暂时不可用，请稍后再试。")
        finally:
            self._active -= 1


@dataclass(frozen=True)
class ChatOutcome:
    text: str
    meme_resource_id: str | None = None


def _bounded_content(message: dict, limit: int) -> str:
    if not isinstance(message, dict):
        raise ValueError("Invalid model message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return "AI 服务没有返回有效内容，请稍后再试。"
    return content.strip()[:limit]
