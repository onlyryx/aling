from aling.services.status import ConnectionStatus


async def test_status_does_not_infer_account_risk_or_online_state():
    status = ConnectionStatus()

    class FakeBot:
        async def call_api(self, action):
            assert action == "get_status"
            return {"online": None, "good": True}

    assert not status.connected
    assert await status.onebot_status(FakeBot()) == ("未知", "是")


async def test_status_suppresses_api_failure_and_tracks_only_event_time():
    status = ConnectionStatus()

    class FakeBot:
        async def call_api(self, action):
            raise RuntimeError("private details must not escape")

    status.event_received()
    assert status.last_event_at is not None
    assert await status.onebot_status(FakeBot()) == ("未知", "未知")
