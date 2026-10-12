import base64
from typing import Annotated

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from aling.core.logging import log
from aling.core.rate_limit import RateLimiter
from aling.runtime import get_runtime
from aling.services.memes import MemeCatalogError, parse_group_meme_command
from aling.services.online_memes import TemporaryMemeRef
from aling.services.search_budget import TaskBudget, deliver_with_budget

__plugin_meta__ = PluginMetadata(
    name="表情包",
    description="按模式查找并发送本地或获准的在线静态表情包",
    usage="/meme [--mode local|auto|online] <关键词>",
    extra={"aling_feature": "meme"},
    supported_adapters={"~onebot.v11"},
)

_limiter = RateLimiter(cooldown=2, limit=30)


async def enabled_group(event) -> bool:
    return isinstance(event, GroupMessageEvent) and await get_runtime().features.enabled(
        event.group_id, "meme"
    )


meme_command = on_command("meme", rule=enabled_group, priority=5, block=True)


@meme_command.handle()
async def meme_handler(event: GroupMessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    runtime = get_runtime()
    raw_args = args.extract_plain_text().strip()
    try:
        mode, query = await parse_group_meme_command(runtime.store, event.group_id, raw_args)
    except MemeCatalogError:
        await meme_command.finish(
            MessageSegment.text("用法：/meme [--mode local|auto|online] <关键词>")
        )
    except Exception as exc:
        log.warning("Meme mode lookup failed ({})", type(exc).__name__)
        await meme_command.finish(MessageSegment.text("本群表情包模式暂不可用，请稍后再试。"))
    if not query:
        await meme_command.finish(
            MessageSegment.text("用法：/meme [--mode local|auto|online] <关键词>")
        )
    if len(query) > 100:
        await meme_command.finish(MessageSegment.text("关键词太长，请控制在 100 字以内。"))
    if not _limiter.allow(event.group_id):
        await meme_command.finish(MessageSegment.text("表情包搜索太频繁了，稍等一会儿再试。"))
    budget = TaskBudget.start() if mode != "local" else None
    task_context = None
    if mode != "local" and runtime.search_policy is not None:
        try:
            task_context = await runtime.search_policy.create_task_from_event(event)
        except Exception:
            await meme_command.finish(MessageSegment.text("在线表情检索任务无法获准。"))
    try:
        result = await runtime.online_memes.search_and_download(task_context, query, mode, budget)
        if result is None:
            await meme_command.finish(
                MessageSegment.text(
                    "没有找到符合当前来源许可的表情包。"
                    if mode != "local"
                    else "没有找到匹配的本地表情包；可按 docs/memes/README.md 添加素材。"
                )
            )
        if isinstance(result, TemporaryMemeRef):
            if task_context is None:
                raise PermissionError("Online meme task context is unavailable")
            image = await runtime.online_memes.read_for_send(
                result.opaque_id,
                event.group_id,
                event.user_id,
                task_id=task_context.task_id,
            )
        else:
            image = runtime.memes.read_image(result.resource_id)
    except Exception as exc:
        log.warning("Meme lookup failed ({})", type(exc).__name__)
        await meme_command.finish(
            MessageSegment.text(
                "在线表情检索未能完成；可能是来源尚未许可或当前策略已变更。"
                if mode != "local"
                else "本地表情包目录暂不可用，请检查素材清单。"
            )
        )
    encoded = base64.b64encode(image.content).decode("ascii")
    label = result.description
    if isinstance(result, TemporaryMemeRef) and not label:
        label = "在线表情包"
    try:
        message = Message(
            [
                MessageSegment.text(label),
                MessageSegment.image(f"base64://{encoded}"),
            ]
        )
        delivery_budget = budget if isinstance(result, TemporaryMemeRef) else None
        if delivery_budget is not None:
            sent = await deliver_with_budget(delivery_budget, lambda: meme_command.send(message))
            if not sent:
                await meme_command.finish()
        else:
            await meme_command.send(message)
    except Exception as exc:
        log.warning("Meme image send failed ({})", type(exc).__name__)
        await meme_command.finish(MessageSegment.text("图片发送失败，请稍后再试。"))
    await meme_command.finish()
