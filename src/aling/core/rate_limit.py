from collections import deque
from collections.abc import Callable
from time import monotonic


class RateLimiter:
    """Process-local admission gate. Call before any await to reserve a slot."""

    def __init__(
        self,
        cooldown: float,
        limit: int,
        window: float = 3600,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.cooldown, self.limit, self.window = cooldown, limit, window
        self.clock = clock
        self._events: dict[int, deque[float]] = {}

    def allow(self, key: int) -> bool:
        now = self.clock()
        # ponytail: one process; use a shared limiter if multiple bot workers are needed.
        for old_key in list(self._events):
            if now - self._events[old_key][-1] >= max(self.window, self.cooldown):
                del self._events[old_key]
        events = self._events.setdefault(key, deque())
        while events and now - events[0] >= self.window:
            if len(events) == 1 and now - events[0] < self.cooldown:
                break
            events.popleft()
        if events and (now - events[-1] < self.cooldown or len(events) >= self.limit):
            return False
        events.append(now)
        return True
