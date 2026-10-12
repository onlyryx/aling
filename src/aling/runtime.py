import asyncio
from dataclasses import dataclass, field

import httpx

from aling.core.config import Settings
from aling.core.storage import GroupSettingsStore
from aling.services.ai import ChatService, OpenAIClient
from aling.services.features import FeatureService
from aling.services.memes import LocalMemeProvider
from aling.services.proactive import ProactiveSender
from aling.services.scheduler import ProactiveScheduler
from aling.services.sessions import SessionStore
from aling.services.status import ConnectionStatus
from aling.services.tools import ToolRegistry
from aling.services.web import SafeWebClient, WebReadService


@dataclass
class Runtime:
    settings: Settings
    store: GroupSettingsStore
    ai: OpenAIClient
    chat: ChatService
    web: SafeWebClient
    web_read: WebReadService
    memes: LocalMemeProvider
    proactive: ProactiveSender
    scheduler: ProactiveScheduler
    features: FeatureService
    status: ConnectionStatus = field(default_factory=ConnectionStatus)
    sessions: SessionStore = field(default_factory=SessionStore)
    tools: ToolRegistry | None = None
    _session_expiry_task: asyncio.Task | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls, settings: Settings, ai_transport: httpx.AsyncBaseTransport | None = None
    ) -> "Runtime":
        store = GroupSettingsStore(settings.database_path, superusers=settings.superusers)
        ai = OpenAIClient(settings, transport=ai_transport)
        web = SafeWebClient(timeout=settings.web_timeout, max_bytes=settings.web_max_bytes)
        status = ConnectionStatus()
        chat = ChatService(settings, ai, status=status)
        web_read = WebReadService(settings, web, chat, status=status)
        memes = LocalMemeProvider(settings.meme_dir)
        sessions = SessionStore(
            ttl_seconds=settings.ai_session_ttl,
            max_turns=settings.ai_session_max_turns,
            max_chars=settings.ai_session_max_chars,
            max_sessions=settings.ai_session_max_count,
        )
        features = FeatureService(settings, store, sessions=sessions)
        proactive = ProactiveSender(settings, store)
        return cls(
            settings,
            store,
            ai,
            chat,
            web,
            web_read,
            memes,
            proactive,
            ProactiveScheduler(settings, store, proactive),
            features,
            status,
            sessions,
            ToolRegistry(features, web_read, memes, timeout=settings.ai_tool_timeout),
        )

    async def startup(self) -> None:
        await self.store.initialize()
        await self.features.initialize()
        if self._session_expiry_task is None or self._session_expiry_task.done():
            self._session_expiry_task = asyncio.create_task(self._expire_sessions())

    async def _expire_sessions(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self.sessions.expire()

    async def shutdown(self) -> None:
        await self.scheduler.stop()
        if self._session_expiry_task is not None:
            self._session_expiry_task.cancel()
            try:
                await self._session_expiry_task
            except asyncio.CancelledError:
                pass
            self._session_expiry_task = None
        await self.sessions.clear()
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
