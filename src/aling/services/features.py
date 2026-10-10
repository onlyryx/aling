import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic

from aling.core.config import Settings
from aling.core.permissions import PermissionContext, require_group_admin
from aling.core.storage import FEATURES, GroupSettingsStore
from aling.services.sessions import SessionStore


class FeatureDisabledError(ValueError):
    """Raised when an unavailable or globally disabled feature is enabled."""


class MemoryConfirmationRequired(ValueError):
    """Raised when enabling group memory without its bound, unused consent."""


class MemoryConfirmationExpired(MemoryConfirmationRequired):
    """Raised when memory consent has reached its expiry time."""


@dataclass(frozen=True)
class MemoryConfirmation:
    ticket: str
    notice: str
    expires_in: int


@dataclass(frozen=True)
class FeatureState:
    global_requested: bool
    group_requested: bool | None
    effective: bool
    blocked_by: list[str]


@dataclass(frozen=True)
class _PendingMemory:
    user_id: int
    group_id: int
    scope: str
    expires_at: float


class FeatureService:
    def __init__(
        self,
        settings: Settings,
        store: GroupSettingsStore,
        *,
        sessions: SessionStore | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings
        self.store = store
        self.sessions = sessions
        self.clock = clock
        self._global_overrides: dict[str, bool] = {}
        self._memory_confirmations: dict[str, _PendingMemory] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._global_overrides = await self.store.get_global_feature_settings()

    def globally_enabled(self, feature: str) -> bool:
        if feature not in FEATURES:
            raise ValueError("Unknown feature")
        configured = feature not in {"ai", "memory"} or self.settings.ai_configured
        return (
            configured
            and self._global_overrides.get(feature, False)
            and (feature != "memory" or self.globally_enabled("ai"))
        )

    async def enabled(self, group_id: int, feature: str) -> bool:
        return (await self.state(group_id, feature)).effective

    async def state(self, group_id: int | None, feature: str) -> FeatureState:
        if feature not in FEATURES:
            raise ValueError("Unknown feature")
        async with self._lock:
            global_requested = self._global_overrides.get(feature, False)
            group_requested = None
            blocked_by = []
            if not global_requested:
                blocked_by.append("global_off")
            if feature in {"ai", "memory"} and not self.settings.ai_configured:
                blocked_by.append("ai_credentials_missing")
            if feature == "memory" and not self._global_overrides.get("ai", False):
                blocked_by.append("global_ai_off")
            if group_id is not None:
                group_requested = await self.store.is_feature_enabled(
                    group_id, feature, global_enabled=True, default_enabled=False
                )
                if not self.settings.group_allowed(group_id):
                    blocked_by.append("scope_restricted")
                if not group_requested:
                    blocked_by.append("group_off")
                if feature == "memory" and not await self.store.is_feature_enabled(
                    group_id, "ai", global_enabled=True, default_enabled=False
                ):
                    blocked_by.append("group_ai_off")
            return FeatureState(global_requested, group_requested, not blocked_by, blocked_by)

    async def available(self, group_id: int) -> set[str]:
        return {feature for feature in FEATURES if await self.enabled(group_id, feature)}

    async def set(self, context: PermissionContext, feature: str, enabled: bool) -> None:
        if feature not in FEATURES:
            raise ValueError("Unknown feature")
        require_group_admin(context, self.settings.superusers, self.settings.allowed_groups)
        if type(enabled) is not bool:
            raise ValueError("Feature state must be boolean")
        if feature == "memory" and enabled:
            raise MemoryConfirmationRequired("Use the memory confirmation flow")
        async with self._lock:
            if enabled and not self.globally_enabled(feature):
                raise FeatureDisabledError("Global feature switch is off")
            await self._complete_mutation(self._persist_feature(context.group_id, feature, enabled))

    async def set_global(self, user_id: int, feature: str, enabled: bool) -> None:
        if user_id not in self.settings.superusers:
            raise PermissionError("Only configured superusers can change global features")
        if feature not in FEATURES:
            raise ValueError("Unknown feature")
        if type(enabled) is not bool:
            raise ValueError("Feature state must be boolean")
        async with self._lock:
            if enabled and feature in {"ai", "memory"} and not self.settings.ai_configured:
                raise FeatureDisabledError(
                    "AI_API_KEY and AI_MODEL must be configured before enabling AI"
                )
            if enabled and feature == "memory" and not self.globally_enabled("ai"):
                raise FeatureDisabledError("Enable the global AI switch before enabling memory")
            await self._complete_mutation(self._persist_feature(None, feature, enabled))

    async def request_memory(self, context: PermissionContext, *, scope: str) -> MemoryConfirmation:
        require_group_admin(context, self.settings.superusers, self.settings.allowed_groups)
        if not scope:
            raise ValueError("Memory confirmation scope is required")
        async with self._lock:
            self._require_memory_available()
            now = self.clock()
            for ticket, pending in list(self._memory_confirmations.items()):
                if pending.expires_at <= now or (
                    pending.user_id == context.user_id
                    and pending.group_id == context.group_id
                    and pending.scope == scope
                ):
                    del self._memory_confirmations[ticket]
            if len(self._memory_confirmations) >= 1000:
                del self._memory_confirmations[next(iter(self._memory_confirmations))]
            ticket = token_urlsafe(32)
            self._memory_confirmations[ticket] = _PendingMemory(
                context.user_id, context.group_id, scope, now + 60
            )
            return MemoryConfirmation(
                ticket,
                f"开启前请确认：只暂存群员明确 @ 阿零的提问与回复，空闲 "
                f"{self.settings.ai_session_ttl} 秒后过期，最迟一分钟内清理；最多 "
                f"{self.settings.ai_session_max_turns} 轮，内容会随后续提问发送给模型服务；"
                "不写入日志或数据库，可用 /forget 清除。"
                "若同意，请在 60 秒内发送 /feature memory confirm。",
                60,
            )

    async def confirm_memory(
        self, context: PermissionContext, *, scope: str, ticket: str | None = None
    ) -> None:
        require_group_admin(context, self.settings.superusers, self.settings.allowed_groups)
        async with self._lock:
            if ticket is None and scope == "qq":
                ticket = next(
                    (
                        key
                        for key, pending in reversed(self._memory_confirmations.items())
                        if pending.user_id == context.user_id
                        and pending.group_id == context.group_id
                        and pending.scope == scope
                    ),
                    None,
                )
            pending = self._memory_confirmations.get(ticket) if ticket is not None else None
            if pending is None or (
                pending.user_id != context.user_id
                or pending.group_id != context.group_id
                or pending.scope != scope
            ):
                raise MemoryConfirmationRequired("No matching memory confirmation")
            if pending.expires_at <= self.clock():
                del self._memory_confirmations[ticket]
                raise MemoryConfirmationExpired("Memory confirmation expired")
            self._require_memory_available()
            await self._complete_mutation(
                self._persist_feature(context.group_id, "memory", True, ticket=ticket)
            )

    async def _complete_mutation(self, operation: Coroutine) -> None:
        """Keep the caller's feature lock until save and its side effects finish."""
        mutation = asyncio.create_task(operation)
        cancelled = False
        while True:
            try:
                await asyncio.shield(mutation)
                break
            except asyncio.CancelledError:
                if mutation.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _persist_feature(
        self, group_id: int | None, feature: str, enabled: bool, *, ticket: str | None = None
    ) -> None:
        if group_id is None:
            await self.store.set_global_feature_enabled(feature, enabled)
        else:
            await self.store.set_feature_enabled(group_id, feature, enabled)
        if feature in {"ai", "memory"} and not enabled:
            await self._clear_memory(group_id)
        if group_id is None:
            self._global_overrides[feature] = enabled
        if ticket is not None:
            del self._memory_confirmations[ticket]

    def _require_memory_available(self) -> None:
        if not self.globally_enabled("memory"):
            raise FeatureDisabledError("Global memory switch is off or unavailable")

    async def _clear_memory(self, group_id: int | None = None) -> None:
        if self.sessions is not None:
            if group_id is None:
                await self.sessions.clear()
            else:
                await self.sessions.clear_group(group_id)
        for ticket, pending in list(self._memory_confirmations.items()):
            if group_id is None or pending.group_id == group_id:
                del self._memory_confirmations[ticket]
