from typing import Annotated

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from aling.core.logging import log
from aling.core.permissions import PermissionContext
from aling.runtime import get_runtime

__plugin_meta__ = PluginMetadata(
    name="基础指令",
    description="帮助、连通性检查与主动发言开关",
    usage="/help、/ping、/proactive on|off",
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


@help_command.handle()
async def help_handler() -> None:
    await help_command.finish(
        MessageSegment.text(
            "阿零（ALING）· Jack233_ / onlyryx\n/help 查看帮助\n/ping 检查连通性\n"
            "群内 @ 我并附上文字：AI 聊天（需维护者启用）\n"
            "/proactive on|off：群管理员设置主动发言开关\n"
            "主动发言还受全局开关限制；当前没有自动发言功能。\n"
            "AI 只收到本次提问，机器人不保存群聊历史。"
        )
    )


@ping_command.handle()
async def ping_handler() -> None:
    await ping_command.finish(MessageSegment.text("pong"))


@proactive_command.handle()
async def proactive_handler(event: MessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    if not isinstance(event, GroupMessageEvent):
        await proactive_command.finish(MessageSegment.text("请在群内使用此命令。"))
    mode = args.extract_plain_text().strip().lower()
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
