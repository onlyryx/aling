from typing import Annotated

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from aling.core.logging import log
from aling.runtime import get_runtime
from aling.services.search import SearchError
from aling.services.search_budget import TaskBudget, deliver_with_budget

__plugin_meta__ = PluginMetadata(
    name="受控搜索",
    description="仅检索部署者批准的用途和来源",
    usage="/search <关键词>",
    extra={"aling_feature": "search"},
    supported_adapters={"~onebot.v11"},
)


search_command = on_command("search", priority=5, block=True)

_ERROR_MESSAGES = {
    "global_off": "联网搜索当前未启用。",
    "group_off": "本群尚未启用联网搜索。",
    "scope_restricted": "这个检索超出本群开放范围。",
    "topic_not_allowed": "这个检索不在当前允许的用途范围内。",
    "intent_uncertain": "暂时无法确认这个检索是否属于允许用途。",
    "classification_unavailable": "用途检查暂时不可用，请稍后再试。",
    "source_not_allowed": "当前没有可用于这个检索的批准来源。",
    "service_unreachable": "搜索服务暂时不可用，请稍后再试。",
    "json_disabled": "搜索服务未启用 JSON 接口，请联系维护者检查配置。",
    "upstream_failed": "搜索服务返回了无效结果，请稍后再试。",
    "no_results": "在已批准的来源中没有找到可用结果。",
    "unsafe_url": "搜索结果链接未通过安全检查，已跳过。",
    "rate_limited": "搜索请求过于频繁，请稍后再试。",
    "budget_exceeded": "本次搜索已达到时间或操作上限。",
    "policy_changed": "搜索权限刚刚发生变化，请重新发起检索。",
}


@search_command.handle()
async def handle_search(
    event: GroupMessageEvent, argument: Annotated[Message, CommandArg()]
) -> None:
    runtime = get_runtime()
    if not isinstance(event, GroupMessageEvent):
        await search_command.finish(MessageSegment.text("请在已开放的群聊中使用 /search。"))
    query = argument.extract_plain_text().strip()
    if not query:
        await search_command.finish(MessageSegment.text("用法：/search <关键词>"))
    budget = TaskBudget.start()
    context = None
    try:
        if runtime.search_policy is None or runtime.search is None:
            raise SearchError("service_unreachable")
        context = await runtime.search_policy.create_task_from_event(event)
        results = await runtime.search.search(
            context,
            query,
            runtime.search_policy.classifier,
            budget,
        )
        response = _render_results(results)
    except SearchError as exc:
        response = _ERROR_MESSAGES.get(exc.code, "搜索未能完成，请稍后再试。")
    except Exception as exc:
        log.warning("Search command failed ({})", type(exc).__name__)
        response = "搜索未能完成，请稍后再试。"
    sent = await deliver_with_budget(
        budget,
        lambda: search_command.send(Message([MessageSegment.text(response)])),
    )
    if not sent and context is not None and runtime.search_policy is not None:
        runtime.search_policy.revoke_task(context.task_id)
    await search_command.finish()


def _render_results(results) -> str:
    if not results.results:
        return "没有在已批准的来源中找到结果。"
    entries = ["搜索结果（仅列出已批准来源，不代表已读取页面正文）："]
    for index, item in enumerate(results.results, start=1):
        entry = f"{index}. {item.title}\n{item.content}\n{item.url}"
        entries.append(entry[:900])
    return "\n\n".join(entries)[:2800]
