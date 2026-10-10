import asyncio
from collections.abc import Awaitable, Callable

from ryxbot.core.config import Settings
from ryxbot.core.permissions import PermissionContext, require_group_admin
from ryxbot.core.rate_limit import RateLimiter
from ryxbot.core.storage import GroupSettingsStore


class ProactiveSender:
    """Mandatory admission gate for future unsolicited messages; no scheduler here."""

    def __init__(self, settings: Settings, store: GroupSettingsStore) -> None:
        self.settings, self.store = settings, store
        self.limiter = RateLimiter(
            settings.proactive_cooldown_seconds, settings.proactive_max_per_hour
        )
        self._lock = asyncio.Lock()

    async def set_enabled(self, context: PermissionContext, enabled: bool) -> None:
        require_group_admin(context, self.settings.superusers, self.settings.allowed_groups)
        async with self._lock:
            await self.store.set_proactive_enabled(context.group_id, enabled)

    async def send(
        self, group_id: int, text: str, send_text: Callable[[int, str], Awaitable[None]]
    ) -> bool:
        # Serialize switch changes and admitted sends so /proactive off takes effect in order.
        async with self._lock:
            if not self.settings.proactive_enabled or not self.settings.group_allowed(group_id):
                return False
            if not text.strip() or not await self.store.is_proactive_enabled(group_id):
                return False
            if not self.limiter.allow(group_id):
                return False
            async with asyncio.timeout(10):
                await send_text(group_id, text[:4000])
            return True
