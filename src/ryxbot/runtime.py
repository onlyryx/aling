from dataclasses import dataclass

import httpx

from ryxbot.core.config import Settings
from ryxbot.core.storage import GroupSettingsStore
from ryxbot.services.ai import ChatService, OpenAIClient
from ryxbot.services.proactive import ProactiveSender
from ryxbot.services.web import SafeWebClient


@dataclass
class Runtime:
    settings: Settings
    store: GroupSettingsStore
    ai: OpenAIClient
    chat: ChatService
    web: SafeWebClient
    proactive: ProactiveSender

    @classmethod
    def create(
        cls, settings: Settings, ai_transport: httpx.AsyncBaseTransport | None = None
    ) -> "Runtime":
        store = GroupSettingsStore(settings.database_path)
        ai = OpenAIClient(settings, transport=ai_transport)
        return cls(
            settings,
            store,
            ai,
            ChatService(settings, ai),
            SafeWebClient(timeout=settings.web_timeout, max_bytes=settings.web_max_bytes),
            ProactiveSender(settings, store),
        )

    async def startup(self) -> None:
        await self.store.initialize()

    async def shutdown(self) -> None:
        await self.ai.aclose()
        await self.web.aclose()


_runtime: Runtime | None = None


def set_runtime(runtime: Runtime) -> None:
    global _runtime
    if _runtime is not None:
        raise RuntimeError("Bot is already initialized in this process")
    _runtime = runtime


def get_runtime() -> Runtime:
    if _runtime is None:
        raise RuntimeError("Bot runtime is not initialized")
    return _runtime
