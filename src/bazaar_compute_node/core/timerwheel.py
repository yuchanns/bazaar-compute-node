from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

_NEAR_BITS = 8
_NEAR_SIZE = 1 << _NEAR_BITS
_LEVEL_BITS = 6
_LEVEL_SIZE = 1 << _LEVEL_BITS
_LEVEL_COUNT = 4
_MAX_DELAY_TICKS = (1 << (_NEAR_BITS + _LEVEL_BITS * _LEVEL_COUNT)) - 1


class TimerCancelledError(RuntimeError):
    """Raised when a timer is cancelled before it expires."""


class TimerWheelClosedError(RuntimeError):
    """Raised when a timer wheel closes before a timer expires."""


@dataclass(slots=True)
class _TimerEntry:
    timer: Timer
    deadline_tick: int
    generation: int
    level: int
    slot: int


class Timer:
    """One resettable, single-consumer timer owned by a TimerWheel."""

    def __init__(
        self,
        wheel: TimerWheel,
        timer_id: int,
        deadline_tick: int,
    ) -> None:
        self._wheel = wheel
        self._id = timer_id
        self._deadline_tick = deadline_tick
        self._generation = 1
        self._active = True
        self._cancelled = False
        self._closed = False
        self._waiting = False
        self._waiter: asyncio.Future[None] | None = None
        self._expired_generation: int | None = None
        self._expiry_consumed = False

    @property
    def id(self) -> int:
        return self._id

    @property
    def active(self) -> bool:
        return self._active

    @property
    def deadline(self) -> int:
        return self._deadline_tick * self._wheel.tick_ms

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def expired_generation(self) -> int | None:
        return self._expired_generation

    def reset(self, delay_ms: int) -> None:
        if not self._active:
            raise RuntimeError("only an active timer can be reset")
        self._wheel._reschedule(self, delay_ms)

    def cancel(self) -> None:
        if self._cancelled or self._closed:
            return
        if self._active:
            self._wheel._remove(self)
        self._active = False
        self._cancelled = True
        waiter = self._waiter
        if waiter is not None and not waiter.done():
            waiter.set_exception(TimerCancelledError("timer was cancelled"))

    async def wait(self) -> None:
        if self._waiting:
            raise RuntimeError("timer already has a waiter")
        if self._closed:
            raise TimerWheelClosedError("timer wheel is closed")
        if self._cancelled:
            raise TimerCancelledError("timer was cancelled")
        if self._expired_generation is not None:
            if self._expiry_consumed:
                raise RuntimeError("timer expiry was already consumed")
            self._expiry_consumed = True
            return
        self._waiting = True
        waiter = asyncio.get_running_loop().create_future()
        self._waiter = waiter
        try:
            await waiter
            self._expiry_consumed = True
        except BaseException:
            if self._active:
                self.cancel()
            raise
        finally:
            self._waiting = False
            self._waiter = None

    def _expire(self, generation: int) -> None:
        if not self._active or generation != self._generation:
            return
        self._active = False
        self._expired_generation = generation
        waiter = self._waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(None)

    def _close(self) -> None:
        self._active = False
        self._closed = True
        waiter = self._waiter
        if waiter is not None and not waiter.done():
            waiter.set_exception(TimerWheelClosedError("timer wheel is closed"))


class TimerWheel:
    """A process-local hierarchical timing wheel driven by one asyncio task."""

    def __init__(self) -> None:
        self._tick_ms = 10
        self._current_tick = time.monotonic_ns() // (self._tick_ms * 1_000_000)
        self._near: list[dict[int, _TimerEntry]] = [{} for _ in range(_NEAR_SIZE)]
        self._levels: list[list[dict[int, _TimerEntry]]] = [
            [{} for _ in range(_LEVEL_SIZE)] for _ in range(_LEVEL_COUNT)
        ]
        self._entries: dict[int, _TimerEntry] = {}
        self._next_timer_id = 1
        self._poke = asyncio.Event()
        self._waiting_poke: asyncio.Event | None = None
        self._driver_task: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False

    @property
    def tick_ms(self) -> int:
        return self._tick_ms

    @property
    def maximum_delay_ms(self) -> int:
        return _MAX_DELAY_TICKS * self._tick_ms

    @property
    def now_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise TimerWheelClosedError("timer wheel is closed")
        self._started = True
        self._current_tick = time.monotonic_ns() // (self._tick_ms * 1_000_000)
        self._driver_task = asyncio.create_task(
            self._run(),
            name="bcn-timer-wheel",
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._started = False
        driver_task = self._driver_task
        self._driver_task = None
        if driver_task is not None:
            driver_task.cancel()
            await asyncio.gather(driver_task, return_exceptions=True)
        entries = tuple(self._entries.values())
        self._entries.clear()
        for bucket in self._near:
            bucket.clear()
        for level in self._levels:
            for bucket in level:
                bucket.clear()
        for entry in entries:
            entry.timer._close()

    def create(self, delay_ms: int) -> Timer:
        self._require_running()
        delay_ticks = self._delay_ticks(delay_ms)
        self._advance_to(time.monotonic_ns() // (self._tick_ms * 1_000_000))
        deadline_tick = self._current_tick + delay_ticks
        timer = Timer(self, self._next_timer_id, deadline_tick)
        self._next_timer_id += 1
        self._insert(timer, deadline_tick, timer.generation)
        if self._waiting_poke:
            self._waiting_poke.set()
        return timer

    async def _run(self) -> None:
        while True:
            if not self._entries:
                self._waiting_poke = self._poke
                self._poke.clear()
                try:
                    await self._poke.wait()
                finally:
                    self._waiting_poke = None
                    self._poke.clear()
                continue
            await asyncio.sleep(self._tick_ms / 1_000)
            self._advance_to(time.monotonic_ns() // (self._tick_ms * 1_000_000))

    def _advance_to(self, target_tick: int) -> None:
        elapsed = target_tick - self._current_tick
        if elapsed <= 0:
            return
        if elapsed >= _NEAR_SIZE and elapsed > len(self._entries):
            self._rebuild(target_tick)
            return
        while self._current_tick < target_tick:
            self._current_tick += 1
            if self._current_tick & (_NEAR_SIZE - 1) == 0:
                self._cascade(0)
            self._expire_near(self._current_tick & (_NEAR_SIZE - 1))

    def _rebuild(self, target_tick: int) -> None:
        entries = tuple(self._entries.values())
        self._entries.clear()
        for bucket in self._near:
            bucket.clear()
        for level in self._levels:
            for bucket in level:
                bucket.clear()
        self._current_tick = target_tick
        for entry in entries:
            if entry.deadline_tick <= target_tick:
                entry.timer._expire(entry.generation)
            else:
                self._insert(entry.timer, entry.deadline_tick, entry.generation)

    def _cascade(self, level_index: int) -> None:
        shift = _NEAR_BITS + level_index * _LEVEL_BITS
        slot = (self._current_tick >> shift) & (_LEVEL_SIZE - 1)
        bucket = self._levels[level_index][slot]
        entries = tuple(bucket.values())
        bucket.clear()
        for entry in entries:
            self._entries.pop(entry.timer.id, None)
            self._insert(entry.timer, entry.deadline_tick, entry.generation)
        if slot == 0 and level_index + 1 < _LEVEL_COUNT:
            self._cascade(level_index + 1)

    def _expire_near(self, slot: int) -> None:
        bucket = self._near[slot]
        entries = tuple(bucket.values())
        bucket.clear()
        for entry in entries:
            self._entries.pop(entry.timer.id, None)
            if entry.deadline_tick <= self._current_tick:
                entry.timer._expire(entry.generation)
            else:
                self._insert(entry.timer, entry.deadline_tick, entry.generation)

    def _reschedule(self, timer: Timer, delay_ms: int) -> None:
        self._require_running()
        delay_ticks = self._delay_ticks(delay_ms)
        self._advance_to(time.monotonic_ns() // (self._tick_ms * 1_000_000))
        if not timer.active:
            raise RuntimeError("only an active timer can be reset")
        self._remove(timer)
        timer._generation += 1
        timer._deadline_tick = self._current_tick + delay_ticks
        timer._expired_generation = None
        timer._expiry_consumed = False
        self._insert(timer, timer._deadline_tick, timer.generation)

    def _insert(self, timer: Timer, deadline_tick: int, generation: int) -> None:
        delta = deadline_tick - self._current_tick
        if delta < _NEAR_SIZE:
            level = -1
            slot = deadline_tick & (_NEAR_SIZE - 1)
            bucket = self._near[slot]
        else:
            level = min(
                (delta.bit_length() - _NEAR_BITS - 1) // _LEVEL_BITS,
                _LEVEL_COUNT - 1,
            )
            shift = _NEAR_BITS + level * _LEVEL_BITS
            slot = (deadline_tick >> shift) & (_LEVEL_SIZE - 1)
            bucket = self._levels[level][slot]
        entry = _TimerEntry(timer, deadline_tick, generation, level, slot)
        bucket[timer.id] = entry
        self._entries[timer.id] = entry

    def _remove(self, timer: Timer) -> None:
        entry = self._entries.pop(timer.id, None)
        if entry is None:
            return
        if entry.level == -1:
            self._near[entry.slot].pop(timer.id, None)
        else:
            self._levels[entry.level][entry.slot].pop(timer.id, None)

    def _delay_ticks(self, delay_ms: int) -> int:
        if isinstance(delay_ms, bool) or not isinstance(delay_ms, int):
            raise TypeError("delay_ms must be an integer")
        if delay_ms < 0:
            raise ValueError("delay_ms must be non-negative")
        delay_ticks = max(1, (delay_ms + self._tick_ms - 1) // self._tick_ms)
        if delay_ticks > _MAX_DELAY_TICKS:
            raise ValueError("delay_ms exceeds the timer wheel horizon")
        return delay_ticks

    def _require_running(self) -> None:
        if self._closed:
            raise TimerWheelClosedError("timer wheel is closed")
        if not self._started:
            raise RuntimeError("timer wheel is not started")


__all__ = [
    "Timer",
    "TimerCancelledError",
    "TimerWheel",
    "TimerWheelClosedError",
]
