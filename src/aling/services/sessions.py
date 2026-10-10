from collections import OrderedDict
from collections.abc import Callable
from time import monotonic


class SessionStore:
    """Bounded process-local context for explicit mentions only."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 600,
        max_turns: int = 6,
        max_chars: int = 12_000,
        max_sessions: int = 1000,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.max_sessions = max_sessions
        self.clock = clock
        # ponytail: any clear invalidates all in-flight writes; use per-session versions if needed.
        self.revision = 0
        self._sessions: OrderedDict[tuple[int, int], tuple[list[tuple[str, str]], float]] = (
            OrderedDict()
        )

    async def get(self, group_id: int, user_id: int) -> list[dict[str, str]]:
        key = (group_id, user_id)
        now = self.clock()
        session = self._sessions.get(key)
        if session is None:
            return []
        turns, touched = session
        if now - touched >= self.ttl_seconds:
            del self._sessions[key]
            return []
        self._sessions[key] = (turns, now)
        self._sessions.move_to_end(key)
        return [
            {"role": role, "content": text}
            for user_text, assistant_text in turns
            for role, text in (("user", user_text), ("assistant", assistant_text))
        ]

    async def add(
        self,
        group_id: int,
        user_id: int,
        user_text: str,
        assistant_text: str,
        *,
        expected_revision: int | None = None,
    ) -> None:
        if expected_revision is not None and expected_revision != self.revision:
            return
        key = (group_id, user_id)
        now = self.clock()
        await self.expire()
        turns, _ = self._sessions.get(key, ([], now))
        turns.append((user_text, assistant_text))
        while len(turns) > self.max_turns:
            turns.pop(0)
        while turns and sum(len(user + answer) for user, answer in turns) > self.max_chars:
            if len(turns) > 1:
                turns.pop(0)
            else:
                user, answer = turns[0]
                budget = self.max_chars
                answer = answer[-min(len(answer), budget // 2) :] if answer else ""
                user = user[-max(0, budget - len(answer)) :]
                turns[0] = (user, answer)
                break
        self._sessions[key] = (turns, now)
        self._sessions.move_to_end(key)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)

    async def forget(self, group_id: int, user_id: int) -> bool:
        self.revision += 1
        return self._sessions.pop((group_id, user_id), None) is not None

    async def clear_group(self, group_id: int) -> None:
        self.revision += 1
        for key in list(self._sessions):
            if key[0] == group_id:
                del self._sessions[key]

    async def clear(self) -> None:
        self.revision += 1
        self._sessions.clear()

    async def expire(self) -> None:
        now = self.clock()
        for key, (_, touched) in list(self._sessions.items()):
            if now - touched >= self.ttl_seconds:
                del self._sessions[key]
