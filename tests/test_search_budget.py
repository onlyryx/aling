import asyncio

import pytest

from aling.core.storage import SearchLimits
from aling.services.search_budget import (
    BudgetExceeded,
    SearchAdmission,
    SearchQuotaLimits,
    TaskBudget,
    TaskBudgetLimits,
    deliver_with_budget,
)


def test_task_budget_stops_work_at_57_seconds_and_keeps_send_window():
    budget = TaskBudget.start(100.0, TaskBudgetLimits())

    assert budget.remaining_work_seconds(100.0) == 57.0
    assert budget.remaining_work_seconds(110.0) == 47.0
    assert budget.remaining_work_seconds(157.0) == 0.0
    assert budget.can_send(159.999)
    assert not budget.can_send(160.0)
    with pytest.raises(BudgetExceeded):
        budget.reserve_external_operation("search", now=157.0)


def test_task_budget_caps_model_requests_and_external_operations():
    budget = TaskBudget.start(0, TaskBudgetLimits())

    for _ in range(5):
        budget.reserve_model_request(now=1)
    with pytest.raises(BudgetExceeded):
        budget.reserve_model_request(now=1)

    for kind in ("search", "read", "read", "image"):
        budget.reserve_external_operation(kind, now=1)
    with pytest.raises(BudgetExceeded):
        budget.reserve_external_operation("read", now=1)

    for _ in range(3):
        budget.reserve_tool_round(now=1)
    with pytest.raises(BudgetExceeded):
        budget.reserve_tool_round(now=1)


def test_policy_limits_only_narrow_task_budget():
    budget = TaskBudget.start(10, TaskBudgetLimits())
    budget.apply_policy_limits(
        SearchLimits(
            task_seconds=30,
            work_seconds=27,
            max_model_requests=2,
            max_external_operations=1,
            max_tool_rounds=1,
            max_searches=1,
            max_results=1,
            request_timeout_seconds=3,
        )
    )

    assert budget.deadline == 40
    assert budget.work_deadline == 37
    assert budget.limits.model_requests == 2
    assert budget.limits.external_operations == 1
    assert budget.limits.max_results == 1


def test_task_budget_allows_only_one_search_and_two_page_reads():
    budget = TaskBudget.start(0, TaskBudgetLimits())

    budget.reserve_external_operation("search", now=1)
    with pytest.raises(BudgetExceeded):
        budget.reserve_external_operation("search", now=1)

    budget.reserve_external_operation("read", now=1)
    budget.reserve_external_operation("read", now=1)
    with pytest.raises(BudgetExceeded):
        budget.reserve_external_operation("read", now=1)


async def test_delivery_is_bounded_by_the_reserved_deadline_window():
    budget = TaskBudget.start(0, TaskBudgetLimits())
    completed = []

    async def send():
        await asyncio.sleep(0.02)
        completed.append(True)

    assert await deliver_with_budget(budget, send, now=59.0)
    assert completed == [True]
    assert not await deliver_with_budget(budget, send, now=60.0)


async def test_delivery_timeout_cancels_the_send_operation():
    budget = TaskBudget.start(0, TaskBudgetLimits())
    cancelled = asyncio.Event()

    async def send():
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.set()

    assert not await deliver_with_budget(budget, send, now=59.99)
    assert cancelled.is_set()


async def test_admission_enforces_cross_group_user_cooldown_and_hourly_quotas():
    now = [1000.0]
    admission = SearchAdmission(clock=lambda: now[0])
    async with admission.slot(group_id=20, user_id=7):
        pass

    now[0] += 1
    with pytest.raises(BudgetExceeded, match="cooldown"):
        async with admission.slot(group_id=21, user_id=7):
            pytest.fail("user cooldown must apply across groups")

    now[0] += 15
    constrained = SearchAdmission(
        clock=lambda: now[0],
        limits=SearchQuotaLimits(user_cooldown_seconds=0, user_hourly=1),
    )
    async with constrained.slot(group_id=20, user_id=7):
        pass
    with pytest.raises(BudgetExceeded, match="hourly quota"):
        async with constrained.slot(group_id=21, user_id=7):
            pass
    now[0] += 3600
    async with constrained.slot(group_id=20, user_id=7):
        pass


async def test_admission_respects_cooldown_longer_than_instance_default():
    now = [1000.0]
    admission = SearchAdmission(clock=lambda: now[0])
    limits = SearchQuotaLimits(user_cooldown_seconds=60)

    async with admission.slot(group_id=20, user_id=7, limits=limits):
        pass

    now[0] += 16
    with pytest.raises(BudgetExceeded, match="cooldown"):
        async with admission.slot(group_id=21, user_id=7, limits=limits):
            pytest.fail("the policy cooldown must outlive the instance default")


def test_default_search_quota_limits_match_the_approved_plan():
    assert SearchQuotaLimits() == SearchQuotaLimits(
        user_cooldown_seconds=15,
        user_hourly=10,
        group_hourly=30,
        instance_hourly=100,
        instance_concurrency=2,
        group_concurrency=1,
    )


async def test_admission_limits_instance_and_group_concurrency():
    admission = SearchAdmission(instance_concurrency=2, group_concurrency=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(group_id):
        async with admission.slot(group_id=group_id, user_id=group_id):
            entered.set()
            await release.wait()

    first = asyncio.create_task(hold(1))
    await entered.wait()
    with pytest.raises(BudgetExceeded, match="(?i)group"):
        async with admission.slot(group_id=1, user_id=2):
            pass
    second = asyncio.create_task(hold(2))
    await asyncio.sleep(0)
    with pytest.raises(BudgetExceeded, match="(?i)instance"):
        async with admission.slot(group_id=3, user_id=3):
            pass
    release.set()
    await asyncio.gather(first, second)
