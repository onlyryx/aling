import re

from nonebot.adapters.onebot.v11 import Message

from aling.core.config import Settings
from aling.core.permissions import PermissionContext, require_group_admin


class GroupModerationService:
    """Narrow, event-authorized OneBot member mute operations."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def mute(self, context: PermissionContext, target_id: int, minutes: int, bot) -> None:
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise ValueError("Mute duration must be between 1 and 1440 minutes")
        await self._set_ban(context, target_id, minutes * 60, bot)

    async def unmute(self, context: PermissionContext, target_id: int, bot) -> None:
        await self._set_ban(context, target_id, 0, bot)

    async def _set_ban(
        self, context: PermissionContext, target_id: int, duration: int, bot
    ) -> None:
        if context.role not in {"owner", "admin"}:
            raise PermissionError("Caller must be a QQ group owner or administrator")
        require_group_admin(context, set(), self.settings.allowed_groups)
        if (
            type(target_id) is not int
            or target_id <= 0
            or target_id in {context.user_id, int(bot.self_id)}
        ):
            raise ValueError("Invalid moderation target")
        group_id = context.group_id
        bot_member = await bot.call_api(
            "get_group_member_info", group_id=group_id, user_id=int(bot.self_id), no_cache=True
        )
        if not isinstance(bot_member, dict) or bot_member.get("role") not in {"owner", "admin"}:
            raise PermissionError("Bot must be a QQ group administrator")
        target_member = await bot.call_api(
            "get_group_member_info", group_id=group_id, user_id=target_id, no_cache=True
        )
        if not isinstance(target_member, dict) or target_member.get("role") != "member":
            raise PermissionError("Only regular members can be muted")
        await bot.call_api("set_group_ban", group_id=group_id, user_id=target_id, duration=duration)


def parse_target(args: Message, *, duration_allowed: bool) -> tuple[int, int] | str:
    mentions = [segment for segment in args if segment.type == "at"]
    if len(mentions) != 1 or mentions[0].data.get("qq") == "all":
        return "请明确 @ 一位普通群成员。"
    if any(segment.type not in {"at", "text"} for segment in args):
        return "参数格式不正确。"
    raw = "".join(segment.data.get("text", "") for segment in args if segment.type == "text")
    tokens = raw.split()
    if duration_allowed:
        if len(tokens) > 1 or (tokens and not re.fullmatch(r"\d{1,4}", tokens[0])):
            return "用法：/mute @成员 [分钟]（1–1440，默认 10 分钟）"
        minutes = int(tokens[0]) if tokens else 10
        if not 1 <= minutes <= 1440:
            return "禁言时长须为 1–1440 分钟。"
    else:
        if tokens:
            return "用法：/unmute @成员"
        minutes = 0
    target = mentions[0].data.get("qq", "")
    if not isinstance(target, str) or not re.fullmatch(r"\d{1,12}", target):
        return "目标成员 ID 无效。"
    return int(target), minutes
