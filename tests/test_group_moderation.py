import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from aling.core.config import Settings
from aling.core.permissions import PermissionContext
from aling.services.group_moderation import GroupModerationService, parse_target


class FakeBot:
    self_id = 99

    def __init__(self, *, bot_role="admin", target_role="member", failure=None):
        self.bot_role = bot_role
        self.target_role = target_role
        self.failure = failure
        self.calls = []

    async def call_api(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "get_group_member_info":
            if self.failure:
                raise RuntimeError("sensitive API response")
            return {
                "role": self.bot_role if kwargs["user_id"] == self_id(self) else self.target_role
            }
        return {"ok": True}


def self_id(bot):
    return bot.self_id


def settings(**kwargs):
    return Settings(_env_file=None, onebot_access_token="test", **kwargs)


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_group_admin_can_mute_regular_member(role):
    bot = FakeBot()
    service = GroupModerationService(settings(allowed_groups={34}))
    await service.mute(PermissionContext(12, 34, role), 23, 10, bot)
    assert bot.calls[-1] == (
        "set_group_ban",
        {"group_id": 34, "user_id": 23, "duration": 600},
    )


async def test_mute_refuses_member_and_superuser_role_bypass():
    bot = FakeBot()
    service = GroupModerationService(settings(superusers={12}, allowed_groups={34}))
    with pytest.raises(PermissionError):
        await service.mute(PermissionContext(12, 34, "member"), 23, 10, bot)
    assert not bot.calls


@pytest.mark.parametrize("target", [12, 99, 0, -1])
async def test_mute_rejects_self_bot_or_invalid_target_before_api(target):
    bot = FakeBot()
    service = GroupModerationService(settings(allowed_groups={34}))
    with pytest.raises(ValueError):
        await service.mute(PermissionContext(12, 34, "admin"), target, 10, bot)
    assert not bot.calls


@pytest.mark.parametrize("minutes", [0, -1, 1441, True, "10"])
async def test_mute_rejects_unsafe_duration(minutes):
    bot = FakeBot()
    service = GroupModerationService(settings(allowed_groups={34}))
    with pytest.raises(ValueError):
        await service.mute(PermissionContext(12, 34, "admin"), 23, minutes, bot)
    assert not bot.calls


async def test_mute_requires_bot_admin_and_regular_target():
    service = GroupModerationService(settings(allowed_groups={34}))
    for bot in [
        FakeBot(bot_role="member"),
        FakeBot(target_role="admin"),
        FakeBot(target_role="owner"),
    ]:
        with pytest.raises(PermissionError):
            await service.mute(PermissionContext(12, 34, "admin"), 23, 10, bot)
        assert all(action != "set_group_ban" for action, _ in bot.calls)


async def test_mute_refuses_out_of_scope_group_and_unmute_uses_zero_seconds():
    service = GroupModerationService(settings(allowed_groups={34}))
    bot = FakeBot()
    with pytest.raises(PermissionError):
        await service.mute(PermissionContext(12, 56, "admin"), 23, 10, bot)
    assert not bot.calls

    await service.unmute(PermissionContext(12, 34, "admin"), 23, bot)
    assert bot.calls[-1] == (
        "set_group_ban",
        {"group_id": 34, "user_id": 23, "duration": 0},
    )


async def test_api_failure_does_not_mutate_and_returns_no_sensitive_error():
    bot = FakeBot(failure="do not expose")
    service = GroupModerationService(settings(allowed_groups={34}))
    with pytest.raises(RuntimeError):
        await service.mute(PermissionContext(12, 34, "admin"), 23, 10, bot)
    assert all(action != "set_group_ban" for action, _ in bot.calls)


def test_explicit_target_parser_defaults_duration_and_rejects_malformed_mentions():
    assert parse_target(Message([MessageSegment.at("23")]), duration_allowed=True) == (23, 10)
    assert parse_target(
        Message([MessageSegment.at("23"), MessageSegment.text(" 30")]), duration_allowed=True
    ) == (23, 30)
    assert isinstance(parse_target(Message(), duration_allowed=True), str)
    assert isinstance(parse_target(Message([MessageSegment.at("all")]), duration_allowed=True), str)
    assert isinstance(
        parse_target(
            Message([MessageSegment.at("23"), MessageSegment.at("24")]), duration_allowed=True
        ),
        str,
    )
    assert isinstance(
        parse_target(
            Message([MessageSegment.at("23"), MessageSegment.text(" 1441")]),
            duration_allowed=True,
        ),
        str,
    )
    assert isinstance(
        parse_target(
            Message([MessageSegment.at("23"), MessageSegment.text(" 10")]),
            duration_allowed=False,
        ),
        str,
    )
