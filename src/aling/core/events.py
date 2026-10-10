from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent


def explicit_group_prompt(event: Event) -> str | None:
    if not isinstance(event, GroupMessageEvent) or event.user_id == event.self_id:
        return None
    if not any(
        segment.type == "at"
        and str(segment.data.get("qq")) == str(event.self_id)
        # QQ replies can insert an incidental @ of the original author.
        and not (index > 0 and event.original_message[index - 1].type == "reply")
        for index, segment in enumerate(event.original_message)
    ):
        return None
    prompt = event.original_message.extract_plain_text().strip()
    if not prompt or prompt.startswith("/"):
        return None
    return prompt
