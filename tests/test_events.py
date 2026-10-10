import pytest
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)

from aling.core.events import explicit_group_prompt


def group_event(message, *, role="member", user_id=12, to_me=False):
    return GroupMessageEvent(
        time=0,
        self_id=99,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        user_id=user_id,
        group_id=34,
        message=message,
        raw_message=str(message),
        font=0,
        sender={"user_id": user_id, "role": role},
        to_me=to_me,
    )


def test_explicit_at_uses_original_segments_after_adapter_strips_at():
    event = group_event(MessageSegment.at(99) + "hello")
    event.message = Message("hello")
    event.to_me = True
    assert explicit_group_prompt(event) == "hello"


@pytest.mark.parametrize(
    "message,user_id,to_me",
    [
        (Message("normal conversation"), 12, False),
        (Message("阿零 hello"), 12, True),
        (MessageSegment.reply(123) + "hello", 12, True),
        (MessageSegment.reply(123) + MessageSegment.at(99) + "just replying", 12, True),
        (MessageSegment.at(88) + "hello", 12, True),
        (MessageSegment.at("all") + "hello", 12, True),
        (MessageSegment.at(99) + "/ping", 12, True),
        (MessageSegment.at(99) + "hello", 99, True),
        (MessageSegment.at(99) + " ", 12, True),
    ],
)
def test_unaddressed_command_self_or_empty_messages_do_not_trigger_ai(message, user_id, to_me):
    assert explicit_group_prompt(group_event(message, user_id=user_id, to_me=to_me)) is None


def test_private_messages_do_not_trigger_ai():
    event = PrivateMessageEvent(
        time=0,
        self_id=99,
        post_type="message",
        message_type="private",
        sub_type="friend",
        message_id=1,
        user_id=12,
        message=MessageSegment.at(99) + "hello",
        raw_message="hello",
        font=0,
        sender={"user_id": 12},
        to_me=True,
    )
    assert explicit_group_prompt(event) is None


def test_reply_with_additional_explicit_mention_can_trigger_ai():
    message = MessageSegment.reply(123) + MessageSegment.at(99) + " "
    message += MessageSegment.at(99) + "new question"
    assert explicit_group_prompt(group_event(message)) == "new question"
