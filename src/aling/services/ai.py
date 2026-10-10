import asyncio
import json

import httpx

from aling.core.config import Settings
from aling.core.logging import log
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

    async def chat(self, prompt: str) -> str:
        config = self.settings
        payload = {
            "model": config.ai_model,
            "messages": [
                {"role": "system", "content": config.ai_system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": config.ai_max_tokens,
            "stream": False,
        }
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
                data = json.loads(await read_limited(response, 1_000_000))
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty or invalid model content")
        return content.strip()[: config.ai_max_output_chars]

    async def aclose(self) -> None:
        await self._client.aclose()


class ChatService:
    def __init__(self, settings: Settings, client: OpenAIClient) -> None:
        self.settings, self.client = settings, client
        self.limiter = RateLimiter(settings.ai_cooldown_seconds, settings.ai_max_per_hour)
        self._active = 0

    async def reply(self, group_id: int, prompt: str) -> str:
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
            return await self.client.chat(prompt)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as exc:
            # Never log exception text: providers may echo URLs, credentials or prompts.
            log.warning("AI request failed ({})", type(exc).__name__)
            return "AI 服务暂时不可用，请稍后再试。"
        finally:
            self._active -= 1
