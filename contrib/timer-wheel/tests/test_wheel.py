from __future__ import annotations

lazy import asyncio
lazy import time
lazy from collections.abc import AsyncIterator
lazy from contextlib import asynccontextmanager

import pytest
from bazaar_contrib_timer import TIMER, ITimer, TimerCancelledError, TimerClosedError
from bazaar_core import (
    CONTRIB_CHANGED,
    Blocked,
    Composition,
    Context,
    Contrib,
    ContribChange,
    ContribEntry,
    ContribHub,
    Failed,
)

from bazaar_contrib_timer_wheel import timer_wheel


@pytest.fixture
async def timer() -> AsyncIterator[ITimer]:
    services: list[ITimer] = []

    @asynccontextmanager
    async def consumer(ctx: Context):
        services.append(ctx.accept(TIMER))
        yield

    async with (
        asyncio.timeout(10),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="wheel", definition=timer_wheel),
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(accepts=[TIMER], lifespan=consumer),
                    ),
                ],
                roots=("consumer",),
            )
        ),
    ):
        yield services[0]


async def test_expiry_and_reset_keep_the_waiter_and_respect_elapsed_time(timer: ITimer):
    await timer.create(0).wait()
    handle = timer.create(timer.maximum_delay_ms)
    generation = handle.generation
    waiter = asyncio.create_task(handle.wait(), eager_start=True)
    started = time.monotonic_ns()
    handle.reset(47)
    await waiter
    assert time.monotonic_ns() - started >= 47_000_000
    assert handle.generation > generation
    assert handle.expired_generation == handle.generation
    assert timer.now_ms >= handle.deadline


async def test_concurrent_timers_cross_the_near_ring_and_resume_from_idle(
    timer: ITimer,
):
    handles = [timer.create(delay) for delay in range(0, 150, 3)]
    handles.append(timer.create(timer.tick_ms * 257))
    await asyncio.gather(*(handle.wait() for handle in handles))
    assert all(handle.expired_generation is not None for handle in handles)
    await asyncio.sleep(0.03)
    await timer.create(0).wait()


async def test_cancel_and_waiter_cancellation_release_long_lived_handles(timer: ITimer):
    for delay in (1 << 14, 1 << 20, 1 << 26):
        handle = timer.create(delay * timer.tick_ms)
        waiter = asyncio.create_task(handle.wait(), eager_start=True)
        handle.cancel()
        handle.cancel()
        with pytest.raises(TimerCancelledError):
            await waiter

    handle = timer.create(timer.maximum_delay_ms)
    waiter = asyncio.create_task(handle.wait(), eager_start=True)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    with pytest.raises(TimerCancelledError):
        await handle.wait()
    await timer.create(0).wait()


async def test_nested_consumers_share_a_provider_and_release_before_its_shutdown():
    services: list[ITimer] = []
    released = asyncio.Event()

    @asynccontextmanager
    async def child(ctx: Context):
        service = ctx.accept(TIMER)
        services.append(service)
        handle = service.create(service.maximum_delay_ms)
        try:
            yield
        finally:
            handle.cancel()
            with pytest.raises(TimerCancelledError):
                await handle.wait()
            await service.create(0).wait()
            released.set()

    @asynccontextmanager
    async def parent(ctx: Context):
        services.append(ctx.accept(TIMER))
        async with ctx.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="child",
                        definition=Contrib(accepts=[TIMER], lifespan=child),
                    )
                ]
            )
        ):
            yield

    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="wheel", definition=timer_wheel),
                    ContribEntry(
                        name="parent",
                        definition=Contrib(accepts=[TIMER], lifespan=parent),
                    ),
                ],
                roots=("parent",),
            )
        ),
    ):
        assert services[0] is services[1]
    assert released.is_set()


async def test_provider_shutdown_completes_pending_waiters_with_the_shared_error():
    services: list[ITimer] = []

    @asynccontextmanager
    async def consumer(ctx: Context):
        services.append(ctx.accept(TIMER))
        yield

    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="wheel", definition=timer_wheel),
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(accepts=[TIMER], lifespan=consumer),
                    ),
                ],
                roots=("consumer",),
            )
        ),
    ):
        handle = services[0].create(60_000)
        waiter = asyncio.create_task(handle.wait(), eager_start=True)
    with pytest.raises(TimerClosedError):
        await waiter
    with pytest.raises(TimerClosedError):
        await handle.wait()


async def test_unexpected_driver_cancellation_closes_handles_and_blocks_consumers():
    services: list[ITimer] = []
    failed = asyncio.Event()
    released = asyncio.Event()

    @asynccontextmanager
    async def consumer(ctx: Context):
        services.append(ctx.accept(TIMER))
        try:
            yield
        finally:
            released.set()

    def changed(ctx: Context, change: ContribChange):
        if any(
            item.name == "wheel" and isinstance(item.state, Failed)
            for item in change.snapshots
        ):
            failed.set()

    async with (
        asyncio.timeout(3),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="wheel", definition=timer_wheel),
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(accepts=[TIMER], lifespan=consumer),
                    ),
                    ContribEntry(
                        name="observer",
                        definition=Contrib(listen={CONTRIB_CHANGED: changed}),
                    ),
                ],
                roots=("consumer", "observer"),
            )
        ),
    ):
        handle = services[0].create(60_000)
        waiter = asyncio.create_task(handle.wait(), eager_start=True)
        provider = next(item for item in hub.snapshots if item.name == "wheel")
        driver = next(
            task
            for task in asyncio.all_tasks()
            if task.get_name() == f"timer-wheel:{provider.id}"
        )
        driver.cancel()
        with pytest.raises(TimerClosedError):
            await waiter
        await failed.wait()
        await released.wait()
        assert driver.done()
        assert any(
            item.name == "consumer" and isinstance(item.state, Blocked)
            for item in hub.snapshots
        )
