import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aling.core.config import Settings
from aling.core.logging import log
from aling.core.permissions import PermissionContext, require_group_admin
from aling.core.storage import GroupSettingsStore, ScheduledMessage
from aling.services.proactive import ProactiveSender


class ProactiveScheduler:
    def __init__(
        self, settings: Settings, store: GroupSettingsStore, sender: ProactiveSender
    ) -> None:
        self.settings, self.store, self.sender = settings, store, sender
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._daily_sent: dict[tuple[int, str], int] = {}

    async def set_schedule(
        self, context: PermissionContext, hour: int, minute: int, text: str
    ) -> None:
        require_group_admin(context, self.settings.superusers, self.settings.allowed_groups)
        async with self._lock:
            await self.store.set_schedule(
                context.group_id, hour, minute, text, owner_id=context.user_id
            )

    async def delete_schedule(self, context: PermissionContext) -> bool:
        require_group_admin(context, self.settings.superusers, self.settings.allowed_groups)
        async with self._lock:
            return await self.store.delete_schedule(context.group_id)

    async def get_schedule(self, group_id: int) -> ScheduledMessage | None:
        return await self.store.get_schedule(group_id)

    async def run_due(
        self,
        aware_now: datetime,
        send_text: Callable[[int, str], Awaitable[None]],
        *,
        connected: bool = True,
    ) -> int:
        if aware_now.tzinfo is None or aware_now.utcoffset() is None:
            raise ValueError("Scheduler requires a timezone-aware datetime")
        if not connected or not self.settings.proactive_enabled:
            return 0
        zone = ZoneInfo(self.settings.proactive_timezone)
        local_now = aware_now.astimezone(zone)
        local_date = local_now.date().isoformat()
        naive_now = local_now.replace(tzinfo=None)
        sent = 0
        async with self._lock:
            self._daily_sent = {
                key: value for key, value in self._daily_sent.items() if key[1] == local_date
            }
            for schedule in await self.store.list_schedules():
                if not self.settings.group_allowed(schedule.group_id):
                    continue
                if not await self.store.is_proactive_enabled(schedule.group_id):
                    continue
                scheduled = datetime.combine(local_now.date(), time(schedule.hour, schedule.minute))
                if scheduled > naive_now:
                    scheduled -= timedelta(days=1)
                elapsed = (naive_now - scheduled).total_seconds()
                if not 0 <= elapsed <= 300 or _is_quiet(
                    local_now.timetz().replace(tzinfo=None), self.settings
                ):
                    continue
                daily_key = (schedule.group_id, local_date)
                if self._daily_sent.get(daily_key, 0) >= self.settings.proactive_max_per_day:
                    continue
                if not await self.store.claim_schedule(
                    schedule.group_id,
                    local_date,
                    expected=schedule,
                    occurrence_date=scheduled.date().isoformat(),
                ):
                    continue
                try:
                    delivered = await self.sender.send(schedule.group_id, schedule.text, send_text)
                except Exception as exc:
                    log.warning("Scheduled proactive send failed ({})", type(exc).__name__)
                    continue
                if delivered:
                    self._daily_sent[daily_key] = self._daily_sent.get(daily_key, 0) + 1
                    sent += 1
        return sent

    async def start(
        self,
        send_text: Callable[[int, str], Awaitable[None]],
        is_connected: Callable[[], bool],
    ) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(send_text, is_connected))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(
        self,
        send_text: Callable[[int, str], Awaitable[None]],
        is_connected: Callable[[], bool],
    ) -> None:
        while True:
            try:
                await self.run_due(datetime.now(UTC), send_text, connected=is_connected())
            except Exception as exc:
                log.warning("Proactive scheduler tick failed ({})", type(exc).__name__)
            await asyncio.sleep(60)


def _is_quiet(local_time: time, settings: Settings) -> bool:
    start, end = settings.proactive_quiet_start, settings.proactive_quiet_end
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end
