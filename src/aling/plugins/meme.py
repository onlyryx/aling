import base64
from typing import Annotated

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from aling.core.logging import log
from aling.core.rate_limit import RateLimiter
from aling.runtime import get_runtime
from aling.services.memes import MemeCatalogError

__plugin_meta__ = PluginMetadata(
    name="本地表情包",
    description="按关键词查找并发送本地表情包",
    usage="/meme <关键词>",
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
    query = args.extract_plain_text().strip()
    if not query:
        await meme_command.finish(MessageSegment.text("用法：/meme <关键词>"))
    if len(query) > 100:
        await meme_command.finish(MessageSegment.text("关键词太长，请控制在 100 字以内。"))
    if not _limiter.allow(event.group_id):
        await meme_command.finish(MessageSegment.text("表情包搜索太频繁了，稍等一会儿再试。"))
    try:
        matches = await get_runtime().memes.search(query, limit=1)
        if not matches:
            await meme_command.finish(
                MessageSegment.text(
                    "没有找到匹配的本地表情包；可按 docs/memes/README.md 添加素材。"
                )
            )
        image = get_runtime().memes.read_image(matches[0].resource_id)
    except MemeCatalogError:
        await meme_command.finish(MessageSegment.text("本地表情包目录暂不可用，请检查素材清单。"))
    encoded = base64.b64encode(image.content).decode("ascii")
    try:
        await meme_command.send(
            Message(
                [
                    MessageSegment.text(matches[0].description),
                    MessageSegment.image(f"base64://{encoded}"),
                ]
            )
        )
    except Exception as exc:
        log.warning("Meme image send failed ({})", type(exc).__name__)
        await meme_command.finish(MessageSegment.text("图片发送失败，请稍后再试。"))
    await meme_command.finish()
