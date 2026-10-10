from typing import Annotated

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from aling.core.logging import log
from aling.core.permissions import PermissionContext
from aling.core.rate_limit import RateLimiter
from aling.runtime import get_runtime
from aling.services.group_moderation import GroupModerationService, parse_target

__plugin_meta__ = PluginMetadata(
    name="群管理",
    description="仅由本群管理明确调用的禁言与解除禁言",
    usage="/mute @成员 [分钟]（默认 10 分钟）；/unmute @成员",
    supported_adapters={"~onebot.v11"},
)

_limiter = RateLimiter(cooldown=2, limit=10)


async def in_scope(event) -> bool:
    return (
        isinstance(event, GroupMessageEvent)
        and event.user_id != event.self_id
        and get_runtime().settings.group_allowed(event.group_id)
    )


mute_command = on_command("mute", rule=in_scope, priority=5, block=True)
unmute_command = on_command("unmute", rule=in_scope, priority=5, block=True)


@mute_command.handle()
async def mute_handler(
    event: GroupMessageEvent, args: Annotated[Message, CommandArg()], bot: Bot
) -> None:
    parsed = parse_target(args, duration_allowed=True)
    if isinstance(parsed, str):
        await mute_command.finish(MessageSegment.text(parsed))
    target_id, minutes = parsed
    await _execute(event, target_id, minutes, bot, mute=True)


@unmute_command.handle()
async def unmute_handler(
    event: GroupMessageEvent, args: Annotated[Message, CommandArg()], bot: Bot
) -> None:
    parsed = parse_target(args, duration_allowed=False)
    if isinstance(parsed, str):
        await unmute_command.finish(MessageSegment.text(parsed))
    target_id, _ = parsed
    await _execute(event, target_id, 0, bot, mute=False)


async def _execute(
    event: GroupMessageEvent, target_id: int, minutes: int, bot: Bot, *, mute: bool
) -> None:
    runtime = get_runtime()
    context = PermissionContext(event.user_id, event.group_id, event.sender.role or "member")
    if context.role not in {"owner", "admin"}:
        await (mute_command if mute else unmute_command).finish(
            MessageSegment.text("仅本群群主或管理员可操作。")
        )
    if not _limiter.allow(event.group_id * 1_000_000_000_000 + event.user_id):
        await (mute_command if mute else unmute_command).finish(
            MessageSegment.text("群管理操作太频繁了，请稍后再试。")
        )
    service = GroupModerationService(runtime.settings)
    try:
        if mute:
            await service.mute(context, target_id, minutes, bot)
        else:
            await service.unmute(context, target_id, bot)
    except PermissionError:
        await (mute_command if mute else unmute_command).finish(
            MessageSegment.text("权限不足：请确认机器人拥有本群管理员权限，且目标是普通成员。")
        )
    except Exception as exc:
        log.warning("Group moderation failed ({})", type(exc).__name__)
        await (mute_command if mute else unmute_command).finish(
            MessageSegment.text("群管理操作失败，请稍后检查 NapCat 权限与日志。")
        )
    action = "已禁言" if mute else "已解除禁言"
    suffix = f"，时长 {minutes} 分钟" if mute else ""
    await (mute_command if mute else unmute_command).finish(
        MessageSegment.text(f"{action}成员 {target_id}{suffix}。")
    )
