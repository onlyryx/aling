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
