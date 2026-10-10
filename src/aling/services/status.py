from dataclasses import dataclass
from datetime import UTC, datetime

from nonebot.adapters import Bot


@dataclass
class ConnectionStatus:
    connected: bool = False
    last_event_at: datetime | None = None

    def connected_to(self, bot: Bot) -> None:
        self.connected = True

    def disconnected_from(self, bot: Bot) -> None:
        self.connected = False

    def event_received(self) -> None:
        self.last_event_at = datetime.now(UTC)

    async def onebot_status(self, bot: Bot) -> tuple[str, str]:
        try:
            result = await bot.call_api("get_status")
            return _truth_label(result.get("online")), _truth_label(result.get("good"))
        except Exception:
            return "未知", "未知"


def _truth_label(value: object) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未知"
