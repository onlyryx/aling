from nonebot import on_message
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata

from ryxbot.core.events import explicit_group_prompt
from ryxbot.runtime import get_runtime

__plugin_meta__ = PluginMetadata(
    name="AI 聊天",
    description="仅群内明确 @ 机器人触发的单轮聊天",
    usage="@机器人 提问",
    supported_adapters={"~onebot.v11"},
)


async def addressed(event: Event) -> bool:
    return (
        isinstance(event, GroupMessageEvent)
        and get_runtime().settings.group_allowed(event.group_id)
        and explicit_group_prompt(event) is not None
    )


chat = on_message(rule=addressed, priority=20, block=True)


@chat.handle()
async def chat_handler(event: GroupMessageEvent) -> None:
    prompt = explicit_group_prompt(event)
    if prompt is None:
        return
    answer = await get_runtime().chat.reply(event.group_id, prompt)
    # A model response is text, never a CQ code, command or executable tool call.
    await chat.finish(MessageSegment.text(answer))
