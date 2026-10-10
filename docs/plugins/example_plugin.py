"""A small native NoneBot plugin sketch; load it with EXTRA_PLUGINS."""

from typing import Annotated

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from aling.core.permissions import PermissionContext, require_group_admin
from aling.runtime import get_runtime
from docs.plugins.example_config import ExamplePluginSettings, create_rate_limiter

settings = ExamplePluginSettings()
limiter = create_rate_limiter(settings)

__plugin_meta__ = PluginMetadata(
    name="Example",
    description="Demonstrates a permission-checked plugin",
    usage="/example on|off",
    extra={"aling_feature": "web"},
    supported_adapters={"~onebot.v11"},
)


async def enabled(event: GroupMessageEvent) -> bool:
    return settings.enabled and await get_runtime().features.enabled(event.group_id, "web")


example = on_command("example", rule=enabled, priority=5, block=True)


@example.handle()
async def handle_example(event: GroupMessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    """Use authenticated OneBot fields, enforce scope, and never log message text."""
    runtime = get_runtime()
    context = PermissionContext(event.user_id, event.group_id, event.sender.role or "member")
    try:
        require_group_admin(context, runtime.settings.superusers, runtime.settings.allowed_groups)
    except PermissionError:
        await example.finish(MessageSegment.text("只有本群管理员可操作。"))
    if not limiter.allow(event.user_id):
        await example.finish(MessageSegment.text("操作太频繁，请稍后再试。"))
    if len(str(args)) > 200:
        await example.finish(MessageSegment.text("参数过长，请控制在 200 字以内。"))
    # Pass bounded arguments to a service with its own validation and timeout.
    await example.finish(MessageSegment.text("示例插件已加载。"))


# Test plugins offline with NoneBot TestClient or fake services. Keep command text out of logs.
