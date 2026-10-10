import base64

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, Message, MessageSegment
from nonebot.plugin import PluginMetadata

from aling.core.events import explicit_group_prompt
from aling.core.logging import log
from aling.runtime import get_runtime

__plugin_meta__ = PluginMetadata(
    name="AI 聊天",
    description="仅群内明确 @ 机器人触发聊天；短期记忆默认关闭",
    usage="@机器人 提问",
    extra={"aling_feature": "ai"},
    supported_adapters={"~onebot.v11"},
)


async def addressed(event: Event) -> bool:
    return (
        isinstance(event, GroupMessageEvent)
        and get_runtime().settings.group_allowed(event.group_id)
        and await get_runtime().features.enabled(event.group_id, "ai")
        and explicit_group_prompt(event) is not None
    )


chat = on_message(rule=addressed, priority=20, block=True)


async def forget_scope(event: Event) -> bool:
    return (
        isinstance(event, GroupMessageEvent)
        and event.user_id != event.self_id
        and get_runtime().settings.group_allowed(event.group_id)
    )


forget_command = on_command("forget", rule=forget_scope, priority=5, block=True)


@chat.handle()
async def chat_handler(event: GroupMessageEvent) -> None:
    prompt = explicit_group_prompt(event)
    if prompt is None:
        return
    runtime = get_runtime()
    revision = runtime.sessions.revision
    memory_enabled = await runtime.features.enabled(event.group_id, "memory")
    history = await runtime.sessions.get(event.group_id, event.user_id) if memory_enabled else []
    if runtime.settings.ai_tools_enabled and runtime.tools is not None:
        outcome = await runtime.chat.reply_with_tools(
            event.group_id, event.user_id, prompt, runtime.tools, history=history
        )
    else:
        outcome = None
        answer = await runtime.chat.reply(event.group_id, prompt, history=history)
    if outcome is not None:
        answer = outcome.text
    if memory_enabled and await runtime.features.enabled(event.group_id, "memory"):
        if not _failed_reply(answer):
            await runtime.sessions.add(
                event.group_id, event.user_id, prompt, answer, expected_revision=revision
            )
    # A model response is text, never a CQ code, command or executable tool call.
    message = Message([MessageSegment.text(answer)])
    if (
        outcome
        and outcome.meme_resource_id
        and await runtime.features.enabled(event.group_id, "meme")
    ):
        try:
            image = runtime.memes.read_image(outcome.meme_resource_id)
            message.append(
                MessageSegment.image("base64://" + base64.b64encode(image.content).decode("ascii"))
            )
        except Exception:
            log.warning("AI selected meme could not be sent")
    if len(message) > 1:
        try:
            await chat.send(message)
        except Exception as exc:
            log.warning("AI meme send failed ({})", type(exc).__name__)
            await chat.finish(MessageSegment.text(answer + "\n（表情包发送失败。）"))
        await chat.finish()
    await chat.finish(message)


@forget_command.handle()
async def forget_handler(event: Event) -> None:
    if not isinstance(event, GroupMessageEvent):
        await forget_command.finish(MessageSegment.text("请在已启用短期会话的群内使用。"))
    runtime = get_runtime()
    await runtime.sessions.forget(event.group_id, event.user_id)
    await forget_command.finish(MessageSegment.text("已清除你在本群的短期会话。"))


def _failed_reply(answer: str) -> bool:
    return any(text in answer for text in ("暂时不可用", "请稍后再试", "繁忙", "过于频繁"))
