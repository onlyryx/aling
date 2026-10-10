from typing import Annotated

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from aling.core.logging import log
from aling.runtime import get_runtime
from aling.services.web import UnsafeURL

__plugin_meta__ = PluginMetadata(
    name="网页阅读",
    description="读取公开网页并可选生成摘要",
    usage="/read <URL> [--summary]",
    extra={"aling_feature": "web"},
    supported_adapters={"~onebot.v11"},
)


async def enabled_group(event) -> bool:
    return isinstance(event, GroupMessageEvent) and await get_runtime().features.enabled(
        event.group_id, "web"
    )


read_command = on_command("read", rule=enabled_group, priority=5, block=True)


@read_command.handle()
async def read_handler(event: GroupMessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    raw = args.extract_plain_text().strip()
    summarize = raw.endswith("--summary")
    if summarize:
        raw = raw[: -len("--summary")].strip()
    if not raw:
        await read_command.finish(MessageSegment.text("用法：/read <URL> [--summary]"))
    runtime = get_runtime()
    ai_allowed = await runtime.features.enabled(event.group_id, "ai")
    try:
        result = await runtime.web_read.read(
            raw, group_id=event.group_id, summarize=summarize, ai_enabled=ai_allowed
        )
    except PermissionError:
        await read_command.finish(MessageSegment.text("本群未开放网页阅读。"))
    except RuntimeError:
        await read_command.finish(MessageSegment.text("网页阅读太频繁了，稍等一会儿再试。"))
    except (UnsafeURL, ValueError, httpx.HTTPError, TimeoutError) as exc:
        log.warning("Web read failed ({})", type(exc).__name__)
        await read_command.finish(MessageSegment.text("网页读取失败；请检查链接是否公开可访问。"))
    message = f"{result.title or '网页内容'}\n来源：{result.source}\n{result.text}"
    if summarize:
        if result.used_summary:
            message += "\n（网页正文已发送至配置的模型服务生成摘要。）"
        else:
            message += "\n（未使用 AI 摘要，以上为正文摘录。）"
    await read_command.finish(MessageSegment.text(message))
