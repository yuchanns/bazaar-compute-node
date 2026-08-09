from __future__ import annotations

import asyncio

import pytest

from bazaar_compute_node.core.concurrency import SessionLockRegistry
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus


def test_timeout_budget_requires_finite_positive_boundaries() -> None:
    budget = TimeoutBudget(
        startup_seconds=1,
        provider_call_seconds=2,
        command_seconds=3,
        shutdown_seconds=4,
    )

    assert budget.command_seconds == 3
    with pytest.raises(ValueError, match="provider_call_seconds"):
        TimeoutBudget(
            startup_seconds=1,
            provider_call_seconds=0,
            command_seconds=3,
            shutdown_seconds=4,
        )


def test_provider_result_requires_explicit_unknown_or_failure_reason() -> None:
    confirmed = ProviderCallResult(
        status=ProviderCallStatus.CONFIRMED,
        value="receipt",
    )
    assert confirmed.value == "receipt"

    queued = ProviderCallResult(
        status=ProviderCallStatus.QUEUED,
        value="queue-receipt",
    )
    assert queued.status is ProviderCallStatus.QUEUED

    partial = ProviderCallResult(
        status=ProviderCallStatus.PARTIAL,
        value="receipt-1",
        error_kind="provider_rejected_batch",
    )
    assert partial.value == "receipt-1"

    unknown = ProviderCallResult(
        status=ProviderCallStatus.UNKNOWN,
        error_kind="transport_eof",
    )
    assert unknown.status is ProviderCallStatus.UNKNOWN
    with pytest.raises(ValueError, match="error_kind"):
        ProviderCallResult(status=ProviderCallStatus.FAILED)
    with pytest.raises(ValueError, match="requires a value"):
        ProviderCallResult(
            status=ProviderCallStatus.PARTIAL,
            error_kind="provider_rejected_batch",
        )


@pytest.mark.asyncio
async def test_same_session_operations_share_one_serial_lock() -> None:
    registry = SessionLockRegistry()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()
    order: list[str] = []

    async def first_operation() -> None:
        async with registry.for_session("session-1"):
            order.append("first-enter")
            first_entered.set()
            await release_first.wait()
            order.append("first-exit")

    async def second_operation() -> None:
        await first_entered.wait()
        async with registry.for_session("session-1"):
            order.append("second-enter")
            second_entered.set()

    first_task = asyncio.create_task(first_operation())
    second_task = asyncio.create_task(second_operation())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert not second_entered.is_set()

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert order == ["first-enter", "first-exit", "second-enter"]


@pytest.mark.asyncio
async def test_different_sessions_do_not_share_the_lock() -> None:
    registry = SessionLockRegistry()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def first_operation() -> None:
        async with registry.for_session("session-1"):
            first_entered.set()
            await release_first.wait()

    async def second_operation() -> None:
        await first_entered.wait()
        async with registry.for_session("session-2"):
            second_entered.set()

    first_task = asyncio.create_task(first_operation())
    second_task = asyncio.create_task(second_operation())
    await asyncio.wait_for(second_entered.wait(), timeout=0.1)
    release_first.set()
    await asyncio.gather(first_task, second_task)
