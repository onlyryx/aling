import re
from time import monotonic
from typing import Annotated

from nonebot import get_bots, get_loaded_plugins, on_command, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from aling.core.logging import log
from aling.core.permissions import PermissionContext, require_group_admin
from aling.runtime import get_runtime
from aling.services.features import FeatureDisabledError
from aling.services.help import render_help

__plugin_meta__ = PluginMetadata(
    name="基础指令",
    description="帮助、状态检查、功能开关与定时消息配置",
    usage=(
        "/help、/ping、/status（仅超级用户）、/feature ai|web|meme|memory on|off/confirm、"
        "/feature global ai|web|meme|memory on|off（仅超级用户）、"
        "/proactive on|off|schedule HH:MM <文本>|schedule off|schedule status"
    ),
    supported_adapters={"~onebot.v11"},
)


async def in_scope(event: MessageEvent) -> bool:
    if event.user_id == event.self_id:
        return False
    return not isinstance(event, GroupMessageEvent) or get_runtime().settings.group_allowed(
        event.group_id
    )


help_command = on_command("help", rule=in_scope, priority=5, block=True)
ping_command = on_command("ping", rule=in_scope, priority=5, block=True)
proactive_command = on_command("proactive", rule=in_scope, priority=5, block=True)
feature_command = on_command("feature", rule=in_scope, priority=5, block=True)
status_command = on_command("status", rule=in_scope, priority=5, block=True)
_memory_confirmations: dict[tuple[int, int], float] = {}


async def unknown_command(event: MessageEvent) -> bool:
    return await in_scope(event) and event.get_plaintext().strip().startswith("/")


unknown = on_message(rule=unknown_command, priority=99, block=False)


@unknown.handle()
async def unknown_handler() -> None:
    await unknown.finish(MessageSegment.text("阿零没有识别到这个指令；发送 /help 查看可用命令。"))


@help_command.handle()
async def help_handler(event: MessageEvent) -> None:
    runtime = get_runtime()
    if isinstance(event, GroupMessageEvent):
        enabled = await runtime.features.available(event.group_id)
    else:
        enabled = {
            name
            for name in ("ai", "web", "meme", "memory")
            if runtime.features.globally_enabled(name)
        }
    await help_command.finish(MessageSegment.text(render_help(get_loaded_plugins(), enabled)))


@ping_command.handle()
async def ping_handler() -> None:
    await ping_command.finish(MessageSegment.text("pong"))


@status_command.handle()
async def status_handler(event: MessageEvent) -> None:
    runtime = get_runtime()
    if event.user_id not in runtime.settings.superusers:
        await status_command.finish(MessageSegment.text("仅配置的超级用户可查看运行状态。"))
    bot = next(iter(get_bots().values()), None)
    connected = bot is not None
    line = "OneBot：已连接" if connected else "OneBot：未连接"
    if connected:
        online, good = await runtime.status.onebot_status(bot)
        line += f"；QQ 在线：{online}；运行状态：{good}"
    last = runtime.status.last_event_at
    if last:
        line += f"\n最近收到事件：{last.astimezone().strftime('%H:%M:%S')}"
    else:
        line += "\n尚未收到事件；这不代表账号离线。"
    await status_command.finish(MessageSegment.text(line))


@proactive_command.handle()
async def proactive_handler(event: MessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    if not isinstance(event, GroupMessageEvent):
        await proactive_command.finish(MessageSegment.text("请在群内使用此命令。"))
    raw = args.extract_plain_text().strip()
    if raw.lower().startswith("schedule"):
        await _schedule_command(event, raw)
        return
    mode = raw.lower()
    if mode not in {"on", "off"}:
        await proactive_command.finish(MessageSegment.text("用法：/proactive on|off"))
    runtime = get_runtime()
    context = PermissionContext(event.user_id, event.group_id, event.sender.role or "member")
    try:
        await runtime.proactive.set_enabled(context, mode == "on")
    except PermissionError:
        await proactive_command.finish(
            MessageSegment.text("仅本群群主、管理员或机器人超级用户可操作。")
        )
    except Exception as exc:
        log.error("Group preference update failed ({})", type(exc).__name__)
        await proactive_command.finish(MessageSegment.text("设置暂时无法保存，请稍后再试。"))
    status = "开启" if mode == "on" else "关闭"
    suffix = "（全局开关仍关闭）" if mode == "on" and not runtime.settings.proactive_enabled else ""
    await proactive_command.finish(MessageSegment.text(f"本群主动发言已{status}{suffix}。"))


async def _schedule_command(event: GroupMessageEvent, raw: str) -> None:
    runtime = get_runtime()
    context = PermissionContext(event.user_id, event.group_id, event.sender.role or "member")
    parts = raw.split(maxsplit=2)
    if len(parts) == 2 and parts[1].lower() == "status":
        schedule = await runtime.scheduler.get_schedule(event.group_id)
        if schedule is None:
            await proactive_command.finish(MessageSegment.text("本群尚未设置每日定时消息。"))
        await proactive_command.finish(
            MessageSegment.text(
                f"本群定时消息：{schedule.hour:02d}:{schedule.minute:02d} {schedule.text}"
            )
        )
    if len(parts) == 2 and parts[1].lower() == "off":
        try:
            deleted = await runtime.scheduler.delete_schedule(context)
        except PermissionError:
            await proactive_command.finish(
                MessageSegment.text("仅本群群主、管理员或机器人超级用户可操作。")
            )
        text = "已移除本群定时消息。" if deleted else "本群没有已设置的定时消息。"
        await proactive_command.finish(MessageSegment.text(text))
    if len(parts) != 3 or not re.fullmatch(r"\d{2}:\d{2}", parts[1]):
        await proactive_command.finish(
            MessageSegment.text("用法：/proactive schedule HH:MM <文本>|off|status")
        )
    hour, minute = map(int, parts[1].split(":"))
    try:
        await runtime.scheduler.set_schedule(context, hour, minute, parts[2])
    except PermissionError:
        await proactive_command.finish(
            MessageSegment.text("仅本群群主、管理员或机器人超级用户可操作。")
        )
    except ValueError:
        await proactive_command.finish(MessageSegment.text("时间须有效，文本长度须为 1–500 字。"))
    await proactive_command.finish(
        MessageSegment.text(
            f"已设置每日 {hour:02d}:{minute:02d} 定时消息。"
            "还需全局开关和本群 /proactive on 才会发送。"
        )
    )


@feature_command.handle()
async def feature_handler(event: MessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    parts = args.extract_plain_text().strip().lower().split()
    features = {"ai", "web", "meme", "memory"}
    runtime = get_runtime()
    if parts and parts[0] == "global":
        if len(parts) != 3 or parts[1] not in features or parts[2] not in {"on", "off"}:
            await feature_command.finish(
                MessageSegment.text("用法：/feature global ai|web|meme|memory on|off（仅超级用户）")
            )
        if event.user_id not in runtime.settings.superusers:
            await feature_command.finish(MessageSegment.text("仅配置的超级用户可操作全局功能。"))
        try:
            await runtime.features.set_global(event.user_id, parts[1], parts[2] == "on")
            if parts[1] in {"ai", "memory"} and parts[2] == "off":
                await runtime.sessions.clear()
        except FeatureDisabledError as exc:
            await feature_command.finish(MessageSegment.text(str(exc)))
        except Exception as exc:
            log.error("Global feature update failed ({})", type(exc).__name__)
            await feature_command.finish(MessageSegment.text("设置暂时无法保存，请稍后再试。"))
        status = "开启" if parts[2] == "on" else "关闭"
        await feature_command.finish(
            MessageSegment.text(
                f"全局 {parts[1]} 已{status}；群内仍需 /feature {parts[1]} on|off。"
            )
        )
    if not isinstance(event, GroupMessageEvent):
        await feature_command.finish(MessageSegment.text("请在群内使用此命令。"))
    if (
        len(parts) != 2
        or parts[0] not in features
        or parts[1] not in {"on", "off", "confirm"}
        or (parts[1] == "confirm" and parts[0] != "memory")
    ):
        await feature_command.finish(
            MessageSegment.text("用法：/feature ai|web|meme|memory on|off（记忆启用需先确认）")
        )
    context = PermissionContext(event.user_id, event.group_id, event.sender.role or "member")
    try:
        require_group_admin(context, runtime.settings.superusers, runtime.settings.allowed_groups)
        if parts == ["memory", "on"] and not await runtime.features.enabled(
            event.group_id, "memory"
        ):
            if not runtime.features.globally_enabled("memory"):
                raise FeatureDisabledError("Global memory switch is off")
            _set_memory_confirmation((event.group_id, event.user_id))
            await feature_command.finish(
                MessageSegment.text(
                    f"开启前请确认：只暂存群员明确 @ 阿零的提问与回复，空闲 "
                    f"{runtime.settings.ai_session_ttl} 秒后过期，最迟一分钟内清理；最多 "
                    f"{runtime.settings.ai_session_max_turns} 轮，内容会随后续提问发送给模型服务；"
                    "不写入日志或数据库，可用 /forget 清除。"
                    "若同意，请在 60 秒内发送 /feature memory confirm。"
                )
            )
        if parts == ["memory", "confirm"]:
            expires = _memory_confirmations.pop((event.group_id, event.user_id), 0)
            if expires < monotonic():
                await feature_command.finish(
                    MessageSegment.text("确认已过期；请先运行 /feature memory on 查看说明。")
                )
            await runtime.features.set(context, "memory", True)
        elif parts[1] in {"on", "off"}:
            await runtime.features.set(context, parts[0], parts[1] == "on")
        if parts[0] in {"ai", "memory"} and parts[1] == "off":
            await runtime.sessions.clear_group(event.group_id)
            for key in list(_memory_confirmations):
                if key[0] == event.group_id:
                    del _memory_confirmations[key]
    except PermissionError:
        await feature_command.finish(
            MessageSegment.text("仅本群群主、管理员或机器人超级用户可操作。")
        )
    except FeatureDisabledError:
        await feature_command.finish(
            MessageSegment.text(
                f"此功能尚未由超级用户开启；请先运行 /feature global {parts[0]} on。"
            )
        )
    status = "关闭" if parts[1] == "off" else "开启"
    await feature_command.finish(MessageSegment.text(f"本群 {parts[0]} 功能已{status}。"))


def _set_memory_confirmation(key: tuple[int, int]) -> None:
    now = monotonic()
    for old_key, expires in list(_memory_confirmations.items()):
        if expires < now:
            del _memory_confirmations[old_key]
    if key not in _memory_confirmations and len(_memory_confirmations) >= 1000:
        del _memory_confirmations[next(iter(_memory_confirmations))]
    _memory_confirmations[key] = now + 60
