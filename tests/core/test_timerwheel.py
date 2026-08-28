from __future__ import annotations

import asyncio

import pytest

from bazaar_compute_node.core.timerwheel import (
    TimerCancelledError,
    TimerWheel,
    TimerWheelClosedError,
)


@pytest.mark.asyncio
async def test_timer_waiter_lifecycle() -> None:
    wheel = TimerWheel()
    await wheel.start()
    try:
        # a timer expires on the next driver tick
        timer = wheel.create(0)
        await asyncio.wait_for(timer.wait(), timeout=0.2)
        assert timer.active is False
        assert timer.expired_generation == 1

        # a reset keeps the waiter and replaces the deadline
        timer = wheel.create(20)
        waiter = asyncio.create_task(timer.wait())
        await asyncio.sleep(0)
        timer.reset(80)
        assert timer.generation == 2
        assert timer.active is True
        await asyncio.sleep(0.04)
        assert waiter.done() is False
        await asyncio.wait_for(waiter, timeout=0.2)
        assert timer.expired_generation == 2

        # a timer serves one waiter, and cancelling wakes it
        timer = wheel.create(100)
        first = asyncio.create_task(timer.wait())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="already has a waiter"):
            await timer.wait()
        timer.cancel()
        with pytest.raises(TimerCancelledError):
            await first

        # cancelling the waiter cancels the timer and drops its entry
        timer = wheel.create(1_000)
        waiter = asyncio.create_task(timer.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert timer.active is False
        assert timer.id not in wheel._entries

        # a stale generation cannot expire a timer that was reset
        timer = wheel.create(100)
        stale_generation = timer.generation
        timer.reset(100)
        timer._expire(stale_generation)
        assert timer.active is True
        assert timer.expired_generation is None
    finally:
        await wheel.close()


@pytest.mark.asyncio
async def test_closing_the_wheel_wakes_waiters() -> None:
    wheel = TimerWheel()
    await wheel.start()
    cancelled_timer = wheel.create(1_000)
    closed_timer = wheel.create(1_000)
    cancelled_waiter = asyncio.create_task(cancelled_timer.wait())
    closed_waiter = asyncio.create_task(closed_timer.wait())
    await asyncio.sleep(0)

    cancelled_timer.cancel()
    await wheel.close()

    with pytest.raises(TimerCancelledError):
        await cancelled_waiter
    with pytest.raises(TimerWheelClosedError):
        await closed_waiter
    assert wheel._driver_task is None
    with pytest.raises(TimerWheelClosedError):
        wheel.create(10)


@pytest.mark.asyncio
async def test_wheel_places_cascades_and_rebuilds_timers() -> None:
    wheel = TimerWheel()
    await wheel.start()
    try:
        # each delay lands on its own wheel level
        delays_by_level = {
            -1: 255,
            0: 256,
            1: 1 << 14,
            2: 1 << 20,
            3: 1 << 26,
        }
        for expected_level, delay_ticks in delays_by_level.items():
            timer = wheel.create(delay_ticks * wheel.tick_ms)
            assert wheel._entries[timer.id].level == expected_level

        # a cross-level reset leaves no entry behind in the old bucket
        timer = wheel.create((1 << 26) * wheel.tick_ms)
        assert wheel._entries[timer.id].level == 3
        timer.reset(10)
        assert wheel._entries[timer.id].level == -1
        assert all(
            timer.id not in bucket for level in wheel._levels for bucket in level
        )
        assert any(timer.id in bucket for bucket in wheel._near)

        # a timer cascades into the near ring before it expires
        timer = wheel.create(256 * wheel.tick_ms)
        deadline_tick = timer._deadline_tick
        wheel._advance_to(deadline_tick - 1)
        assert timer.active is True
        wheel._advance_to(deadline_tick)
        await timer.wait()
        assert timer.active is False

        # a large catch-up expires what is due and rebuilds the rest
        expired = wheel.create(10)
        retained = wheel.create(5_000)
        expired_waiter = asyncio.create_task(expired.wait())
        wheel._advance_to(wheel._current_tick + 300)
        await expired_waiter
        assert expired.active is False
        assert retained.active is True
        assert wheel._entries[retained.id].deadline_tick == retained._deadline_tick
    finally:
        await wheel.close()


@pytest.mark.asyncio
async def test_wheel_rejects_invalid_delays() -> None:
    wheel = TimerWheel()
    await wheel.start()
    try:
        with pytest.raises(TypeError, match="integer"):
            wheel.create(1.5)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="non-negative"):
            wheel.create(-1)
        with pytest.raises(ValueError, match="horizon"):
            wheel.create((1 << 32) * wheel.tick_ms)
    finally:
        await wheel.close()
