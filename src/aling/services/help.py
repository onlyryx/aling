from collections.abc import Iterable

from nonebot.plugin import Plugin


def render_help(plugins: Iterable[Plugin], enabled_features: set[str]) -> str:
    entries = []
    for plugin in plugins:
        metadata = plugin.metadata
        if not metadata or not metadata.usage:
            continue
        feature = metadata.extra.get("aling_feature")
        if feature and feature not in enabled_features:
            continue
        entries.append((metadata.name, metadata.usage.strip()))
    lines = ["阿零（ALING）· Jack233_ / onlyryx", *(usage for _, usage in sorted(entries))]
    lines.append("主动发言还受全局开关限制。")
    lines.extend(
        [
            "/feature global search|online_meme on|off：超级用户管理联网全局许可。",
            "/feature search|online_meme on|off：群主、管理员在全局许可内管理本群。",
            "受控联网需本群开启 AI 用途检查；查询会发送给模型与搜索上游。"
            "超级用户须先确认外部数据流，再开启联网；普通成员不能修改来源。",
        ]
    )
    lines.append("/meme 默认仅搜索本地表情包；可用 --mode auto|online 选择在线模式。")
    if "online_meme" in enabled_features:
        lines.append("在线表情已开放；只有服务就绪且来源获准时才能使用。")
    else:
        lines.append("在线表情当前未启用；需开启 meme、online_meme 与 AI 用途检查。")
    if "search" in enabled_features:
        lines.append("联网搜索当前已启用；仍需服务就绪且搜索来源获准。")
    else:
        lines.append("联网搜索当前未启用；需开启全局和本群 search 与 AI 用途检查。")
    if "memory" in enabled_features:
        lines.extend(
            [
                "本群已启用短期会话：仅暂存明确 @ 的个人问答，不保存普通聊天。",
                "/forget：清除你在本群的短期会话。",
            ]
        )
    else:
        lines.append("AI 只收到本次提问，不保存群聊历史。")
    return "\n".join(lines)
