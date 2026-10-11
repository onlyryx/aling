import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic

from nonebot.adapters import Bot

_DIAGNOSTICS = {
    "onebot": {"status_failed", "groups_failed"},
    "ai": {"request_failed", "tool_turn_failed"},
    "web": {"summary_failed", "read_failed"},
    "storage": {"read_failed", "save_failed"},
}
_EXCEPTION_TYPES = {
    "HTTPError",
    "HTTPStatusError",
    "ConnectError",
    "ReadTimeout",
    "TimeoutError",
    "ValueError",
    "KeyError",
    "IndexError",
    "TypeError",
    "RuntimeError",
    "OSError",
    "OperationalError",
    "DatabaseError",
    "UnsafeURL",
    "ActionFailed",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _unknown_qq() -> dict:
    return {"online": None, "good": None, "queried_at": None}


@dataclass
class ConnectionStatus:
    connected: bool = False
    last_event_at: datetime | None = None
    started_at: datetime = field(default_factory=_now)
    bot: Bot | None = field(default=None, repr=False)
    group_snapshot: dict[int, str | None] = field(default_factory=dict)
    groups_queried_at: str | None = None
    groups_truncated: bool = False
    _connection_revision: int = field(default=0, repr=False)
    _qq: dict = field(default_factory=_unknown_qq, repr=False)
    _qq_cached_at: float | None = field(default=None, repr=False)
    _qq_bot: Bot | None = field(default=None, repr=False)
    _diagnostics: list[dict] = field(default_factory=list, repr=False)
    _qq_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _groups_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def connected_to(self, bot: Bot) -> None:
        if self.bot is not bot:
            self._connection_revision += 1
            self._qq_cached_at = None
            self._qq = _unknown_qq()
        self.connected = True
        self.bot = bot

    def disconnected_from(self, bot: Bot) -> None:
        if self.bot is not None and self.bot is not bot:
            return
        self._connection_revision += 1
        self.connected = False
        self.bot = None
        self._qq_cached_at = None
        self._qq = _unknown_qq()

    def event_received(self) -> None:
        self.last_event_at = _now()

    def overview(self, *, version: str) -> dict:
        cutoff = _now() - timedelta(hours=24)
        self._diagnostics = [
            item for item in self._diagnostics if datetime.fromisoformat(item["at"]) > cutoff
        ]
        qq = dict(self._qq)
        if self._qq_cached_at is None or monotonic() - self._qq_cached_at >= 30:
            qq["online"] = qq["good"] = None
        return {
            "version": version,
            "started_at": self.started_at.isoformat(),
            "process_available": True,
            "onebot_connected": self.connected,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "qq": qq,
            "diagnostics": [dict(item) for item in reversed(self._diagnostics)],
        }

    def record_diagnostic(
        self, component: str, code: str, *, exception_type: str | None = None
    ) -> None:
        if code not in _DIAGNOSTICS.get(component, set()):
            raise ValueError("Unknown diagnostic category")
        self.overview(version="")  # Purge expired fixed records; never retain raw error text.
        self._diagnostics.append(
            {
                "at": _now().isoformat(),
                "component": component,
                "code": code,
                "exception_type": exception_type if exception_type in _EXCEPTION_TYPES else None,
            }
        )
        self._diagnostics = self._diagnostics[-100:]

    async def refresh_qq(self, bot: Bot | None) -> dict:
        result = _unknown_qq()
        result["queried_at"] = _now().isoformat()
        revision = self._connection_revision
        try:
            async with asyncio.timeout(10):
                async with self._qq_lock:
                    if (
                        self._qq_cached_at is not None
                        and self._qq_bot is bot
                        and monotonic() - self._qq_cached_at < 30
                    ):
                        return dict(self._qq)
                    try:
                        if bot is not None:
                            payload = await bot.call_api("get_status")
                            if isinstance(payload, dict):
                                for key in ("online", "good"):
                                    result[key] = (
                                        payload.get(key) if type(payload.get(key)) is bool else None
                                    )
                    finally:
                        # Publish while still owning the lock, including unknown query failures.
                        # A waiter that times out before acquiring it must not overwrite this cache.
                        if revision != self._connection_revision:
                            result["online"] = result["good"] = None
                        self._qq, self._qq_bot, self._qq_cached_at = result, bot, monotonic()
                    return dict(result)
        except Exception as exc:
            self.record_diagnostic("onebot", "status_failed", exception_type=type(exc).__name__)
            return dict(result)

    async def refresh_groups(self, bot: Bot | None) -> bool:
        async with self._groups_lock:
            if bot is None:
                return False
            try:
                async with asyncio.timeout(10):
                    payload = await bot.call_api("get_group_list")
                if not isinstance(payload, list):
                    raise ValueError("Invalid group list")
                snapshot = {}
                for group in payload[:1000]:
                    if not isinstance(group, dict):
                        continue
                    group_id = group.get("group_id")
                    if type(group_id) is int and 0 < group_id <= 2**63 - 1:
                        name = group.get("group_name")
                        snapshot[group_id] = name[:200] if isinstance(name, str) else None
                self.group_snapshot = snapshot
                self.groups_queried_at = _now().isoformat()
                self.groups_truncated = len(payload) > 1000
                return True
            except Exception as exc:
                self.record_diagnostic("onebot", "groups_failed", exception_type=type(exc).__name__)
                return False

    async def onebot_status(self, bot: Bot) -> tuple[str, str]:
        result = await self.refresh_qq(bot)
        return _truth_label(result["online"]), _truth_label(result["good"])


def _truth_label(value: object) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未知"
