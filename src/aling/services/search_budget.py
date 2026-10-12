"""Short-lived budgets for one controlled online task."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from time import monotonic


class BudgetExceeded(RuntimeError):
    """A task or admission limit was reached."""

    code = "budget_exceeded"


class AdmissionRejected(BudgetExceeded):
    code = "rate_limited"


@dataclass(frozen=True)
class TaskBudgetLimits:
    task_seconds: float = 60
    work_seconds: float = 57
    reserved_send_seconds: float = 3
    model_requests: int = 5
    external_operations: int = 4
    tool_rounds: int = 3
    searches: int = 1
    page_reads: int = 2
    images: int = 1
    max_results: int = 3
    request_timeout_seconds: float = 10


@dataclass
class TaskBudget:
    started_at: float
    limits: TaskBudgetLimits = field(default_factory=TaskBudgetLimits)
    _model_requests: int = 0
    _tool_rounds: int = 0
    _external_operations: int = 0
    _counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @classmethod
    def start(cls, now: float | None = None, limits: TaskBudgetLimits | None = None) -> TaskBudget:
        return cls(monotonic() if now is None else now, limits or TaskBudgetLimits())

    @property
    def deadline(self) -> float:
        return self.started_at + self.limits.task_seconds

    @property
    def work_deadline(self) -> float:
        return min(
            self.deadline - self.limits.reserved_send_seconds,
            self.started_at + self.limits.work_seconds,
        )

    def apply_policy_limits(self, limits) -> None:
        """Only narrow task limits from the deployment/group policy snapshot."""
        self.limits = TaskBudgetLimits(
            task_seconds=min(self.limits.task_seconds, limits.task_seconds),
            work_seconds=min(self.limits.work_seconds, limits.work_seconds),
            reserved_send_seconds=self.limits.reserved_send_seconds,
            model_requests=min(self.limits.model_requests, limits.max_model_requests),
            external_operations=min(
                self.limits.external_operations, limits.max_external_operations
            ),
            tool_rounds=min(self.limits.tool_rounds, limits.max_tool_rounds),
            searches=min(self.limits.searches, limits.max_searches),
            page_reads=self.limits.page_reads,
            images=self.limits.images,
            max_results=min(self.limits.max_results, limits.max_results),
            request_timeout_seconds=min(
                self.limits.request_timeout_seconds, limits.request_timeout_seconds
            ),
        )

    def reserve_model_request(self, now: float | None = None) -> None:
        current = monotonic() if now is None else now
        if current >= self.work_deadline:
            raise BudgetExceeded("Task work deadline reached")
        if self._model_requests >= self.limits.model_requests:
            raise BudgetExceeded("Model request limit reached")
        self._model_requests += 1

    def reserve_tool_round(self, now: float | None = None) -> None:
        current = monotonic() if now is None else now
        if current >= self.work_deadline:
            raise BudgetExceeded("Task work deadline reached")
        if self._tool_rounds >= self.limits.tool_rounds:
            raise BudgetExceeded("Tool round limit reached")
        self._tool_rounds += 1

    def reserve_external_operation(self, kind: str, now: float | None = None) -> None:
        current = monotonic() if now is None else now
        if current >= self.work_deadline:
            raise BudgetExceeded("Task work deadline reached")
        caps = {
            "search": self.limits.searches,
            "read": self.limits.page_reads,
            "image": self.limits.images,
        }
        if kind not in caps:
            raise BudgetExceeded("Unknown external operation")
        if self._external_operations >= self.limits.external_operations:
            raise BudgetExceeded("External operation limit reached")
        if self._counts[kind] >= caps[kind]:
            raise BudgetExceeded(f"{kind} operation limit reached")
        self._external_operations += 1
        self._counts[kind] += 1

    def remaining_work_seconds(self, now: float | None = None) -> float:
        current = monotonic() if now is None else now
        return max(0.0, self.work_deadline - current)

    def can_send(self, now: float | None = None) -> bool:
        current = monotonic() if now is None else now
        return current < self.deadline


@dataclass(frozen=True)
class SearchQuotaLimits:
    user_cooldown_seconds: float = 15
    user_hourly: int = 10
    group_hourly: int = 30
    instance_hourly: int = 100
    instance_concurrency: int = 2
    group_concurrency: int = 1

    @classmethod
    def from_policy(cls, limits) -> SearchQuotaLimits:
        return cls(
            user_cooldown_seconds=limits.user_cooldown_seconds,
            user_hourly=limits.user_hourly_limit,
            group_hourly=limits.group_hourly_limit,
            instance_hourly=limits.instance_hourly_limit,
            instance_concurrency=limits.instance_concurrency,
            group_concurrency=limits.group_concurrency,
        )


class SearchAdmission:
    """In-process logical-task quotas shared by owner and group entry points."""

    def __init__(
        self,
        *,
        limits: SearchQuotaLimits | None = None,
        clock: Callable[[], float] = monotonic,
        user_hourly_limit: int | None = None,
        instance_concurrency: int | None = None,
        group_concurrency: int | None = None,
    ) -> None:
        self.limits = limits or SearchQuotaLimits()
        if user_hourly_limit is not None:
            self.limits = replace(self.limits, user_hourly=user_hourly_limit)
        if instance_concurrency is not None:
            self.limits = replace(self.limits, instance_concurrency=instance_concurrency)
        if group_concurrency is not None:
            self.limits = replace(self.limits, group_concurrency=group_concurrency)
        self.clock = clock
        self._users: dict[int, deque[float]] = {}
        self._groups: dict[int, deque[float]] = {}
        self._instance: deque[float] = deque()
        self._last_user: dict[int, tuple[float, float]] = {}
        self._active_groups: dict[int, int] = defaultdict(int)
        self._active = 0
        self._lock = asyncio.Lock()

    def _prune(self, bucket: deque[float], now: float) -> None:
        while bucket and now - bucket[0] >= 3600:
            bucket.popleft()

    def _prune_state(self, now: float, user_cooldown_seconds: float) -> None:
        self._prune(self._instance, now)
        for buckets in (self._users, self._groups):
            for identity, bucket in list(buckets.items()):
                self._prune(bucket, now)
                if not bucket:
                    buckets.pop(identity, None)
        for user_id, (last, stored_cooldown) in list(self._last_user.items()):
            if now - last >= max(stored_cooldown, user_cooldown_seconds):
                self._last_user.pop(user_id, None)

    @asynccontextmanager
    async def slot(
        self,
        *,
        group_id: int,
        user_id: int,
        limits: SearchQuotaLimits | None = None,
    ) -> AsyncIterator[None]:
        if type(group_id) is not int or group_id <= 0 or type(user_id) is not int or user_id <= 0:
            raise AdmissionRejected("Invalid search principal")
        now = self.clock()
        quota = limits or self.limits
        async with self._lock:
            self._prune_state(now, quota.user_cooldown_seconds)
            user = self._users.get(user_id, deque())
            group = self._groups.get(group_id, deque())
            last = self._last_user.get(user_id)
            if last is not None and now - last[0] < max(last[1], quota.user_cooldown_seconds):
                raise AdmissionRejected("User cooldown active")
            if len(user) >= quota.user_hourly:
                raise AdmissionRejected("User hourly quota reached")
            if len(group) >= quota.group_hourly:
                raise AdmissionRejected("Group hourly quota reached")
            if len(self._instance) >= quota.instance_hourly:
                raise AdmissionRejected("Instance hourly quota reached")
            if self._active >= quota.instance_concurrency:
                raise AdmissionRejected("Instance concurrency limit reached")
            if self._active_groups[group_id] >= quota.group_concurrency:
                raise AdmissionRejected("Group concurrency limit reached")
            user.append(now)
            group.append(now)
            self._users[user_id] = user
            self._groups[group_id] = group
            self._instance.append(now)
            self._last_user[user_id] = (now, quota.user_cooldown_seconds)
            self._active += 1
            self._active_groups[group_id] += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active -= 1
                self._active_groups[group_id] -= 1
                if not self._active_groups[group_id]:
                    self._active_groups.pop(group_id, None)


async def deliver_with_budget(
    budget: TaskBudget,
    send_operation: Callable[[], Awaitable[object]],
    *,
    now: float | None = None,
) -> bool:
    current = monotonic() if now is None else now
    remaining = budget.deadline - current
    if remaining <= 0:
        return False
    try:
        await asyncio.wait_for(send_operation(), timeout=remaining)
    except TimeoutError:
        return False
    return True
