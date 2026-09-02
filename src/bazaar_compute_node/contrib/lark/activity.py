from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from uuid import uuid4

from ...core.activity import (
    ActivityKind,
    ActivityOutcome,
    ActivityOverview,
    ActivityReducer,
    snapshot_line,
)
from ...core.models import (
    ContextCompactionCompleted,
    ContextCompactionStarted,
    RuntimeOutputEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnUnknown,
    UsageUpdated,
)
from ...core.timerwheel import TimerWheel
from ...i18n import Translator
from ...rendering import TextTemplate
from .api import LarkApi, LarkApiError

_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_RATE_LIMIT_RETRIES = 3
_MAX_TOOL_NAME_BYTES = 64
_CARD_REQUEST_INTERVAL_SECONDS = 0.2
_ACTIVITY_ELEMENT_ID = "activity"
_LOGGER = logging.getLogger(__name__)
_ACTIVITY_TEMPLATE = TextTemplate.from_resource("lark_activity.tpl")
_ACTIVITY_TITLE_TEMPLATE = TextTemplate.from_resource("lark_activity_title.tpl")
_KIND_ICONS = {
    ActivityKind.TOOL_CALL: "setting_outlined",
    ActivityKind.CONTEXT_COMPACTION: "archive_outlined",
}
_OUTCOME_COLORS = {
    ActivityOutcome.RUNNING: "blue",
    ActivityOutcome.COMPLETED: "green",
    ActivityOutcome.FAILED: "red",
    ActivityOutcome.CANCELLED: "neutral",
    ActivityOutcome.UNKNOWN: "orange",
}


@dataclass(frozen=True, slots=True)
class LarkActivityRoute:
    message_id: str
    reply_in_thread: bool


@dataclass(slots=True)
class CardState:
    card_id: str
    provider_message_id: str
    next_sequence: int = 1
    last_request_at: float = 0.0
    writable: bool = True


@dataclass(slots=True)
class _DrainActivity:
    complete: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class _ActivityTurn:
    session_id: str
    turn_id: str
    route: LarkActivityRoute
    api: LarkApi
    reducer: ActivityReducer = field(default_factory=ActivityReducer)
    pending: list[RuntimeOutputEvent | _DrainActivity] = field(default_factory=list)
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    card: CardState | None = None
    desired: dict[str, object] | None = None
    written: dict[str, object] | None = None
    incomplete: bool = False
    terminal: asyncio.Event = field(default_factory=asyncio.Event)


class LarkActivityProjector:
    def __init__(
        self,
        *,
        timer_wheel: TimerWheel,
        translator: Translator,
        report_degraded: Callable[[str, str], None],
    ) -> None:
        self._timer_wheel = timer_wheel
        self._translator = translator
        self._report_degraded = report_degraded
        self._title = translator.text("activity.title")
        self._turns: dict[tuple[str, str], _ActivityTurn] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._rate_lock = asyncio.Lock()
        self._requests_second: list[float] = []
        self._requests_minute: list[float] = []
        self.cards_created = 0
        self.elements_updated = 0
        self.failures = 0
        self.rate_limit_retries = 0
        self.coalesced_updates = 0

    @property
    def active_turns(self) -> int:
        return len(self._turns)

    @property
    def tasks_pending(self) -> int:
        return len(self._tasks)

    def accept(
        self,
        item: RuntimeOutputEvent,
        *,
        route: LarkActivityRoute | None,
        api: LarkApi | None,
    ) -> None:
        if not isinstance(
            item.payload,
            ToolCallStarted
            | ToolCallCompleted
            | ToolCallFailed
            | ContextCompactionStarted
            | ContextCompactionCompleted
            | UsageUpdated
            | TurnCompleted
            | TurnFailed
            | TurnCancelled
            | TurnUnknown,
        ):
            return
        key = self._turn_key(item)
        if key is None:
            return
        turn = self._turns.get(key)
        if turn is None:
            if route is None or api is None:
                return
            turn = _ActivityTurn(
                session_id=item.envelope.session_id,
                turn_id=key[1],
                route=route,
                api=api,
            )
            self._turns[key] = turn
            task = asyncio.create_task(
                self._run_turn(key, turn),
                name=f"bcn-lark-activity-{key[1]}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        turn.pending.append(item)
        turn.wakeup.set()

    async def wait_terminal(self, session_id: str, *, timeout: float) -> None:
        waits = [
            turn.terminal.wait()
            for key, turn in self._turns.items()
            if key[0] == session_id
        ]
        if waits:
            await asyncio.wait_for(asyncio.gather(*waits), timeout=max(0.0, timeout))

    async def drain(
        self,
        session_id: str,
        turn_id: str,
        *,
        timeout: float,
    ) -> None:
        waits = []
        for key, turn in self._turns.items():
            if key != (session_id, turn_id):
                continue
            drain = _DrainActivity()
            turn.pending.append(drain)
            turn.wakeup.set()
            waits.append(drain.complete.wait())
        if waits:
            await asyncio.wait_for(asyncio.gather(*waits), timeout=max(0.0, timeout))

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._turns.clear()

    async def _run_turn(
        self,
        key: tuple[str, str],
        turn: _ActivityTurn,
    ) -> None:
        try:
            while True:
                while not turn.pending:
                    turn.wakeup.clear()
                    if turn.pending:
                        break
                    await turn.wakeup.wait()
                item = turn.pending.pop(0)
                if isinstance(item, _DrainActivity):
                    await self._flush(turn)
                    item.complete.set()
                    continue
                self._absorb(turn, item)
                await self._flush(turn)
                if turn.reducer.overview is not None:
                    self._finish_turn(turn)
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failures += 1
            turn.incomplete = True
            self._report_degraded(turn.session_id, turn.turn_id)
            _LOGGER.exception("Lark activity projector failed")
        finally:
            for item in turn.pending:
                if isinstance(item, _DrainActivity):
                    item.complete.set()
            if self._turns.get(key) is turn:
                self._turns.pop(key, None)
            turn.terminal.set()

    def _absorb(self, turn: _ActivityTurn, item: RuntimeOutputEvent) -> None:
        if turn.reducer.apply(item.payload):
            turn.desired = self._render(turn)

    async def _flush(self, turn: _ActivityTurn) -> None:
        while turn.desired is not None and turn.desired != turn.written:
            await self._wait_for_interval(turn)
            content = turn.desired
            if not await self._write(turn, content):
                return
            turn.written = content

    async def _wait_for_interval(self, turn: _ActivityTurn) -> None:
        card = turn.card
        while card is not None:
            remaining = card.last_request_at + _CARD_REQUEST_INTERVAL_SECONDS
            remaining -= monotonic()
            if remaining <= 0:
                return
            await self._wait_ms(max(1, int(remaining * 1000)))
            while turn.pending:
                item = turn.pending[0]
                if isinstance(item, _DrainActivity):
                    break
                turn.pending.pop(0)
                self.coalesced_updates += 1
                self._absorb(turn, item)

    async def _write(
        self,
        turn: _ActivityTurn,
        content: dict[str, object],
    ) -> bool:
        if turn.card is None:
            return await self._create_card(turn, content)
        card = turn.card
        if not card.writable:
            return False
        element = content
        operation_uuid = uuid4().hex
        sequence = card.next_sequence
        retries = 0
        while True:
            try:
                await self._rate_limit(card)
                await turn.api.update_card_element(
                    card.card_id,
                    _ACTIVITY_ELEMENT_ID,
                    element,
                    uuid=operation_uuid,
                    sequence=sequence,
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except LarkApiError as error:
                if error.http_status == 429 and retries < _MAX_RATE_LIMIT_RETRIES:
                    self.rate_limit_retries += 1
                    await self._wait_ms(100 * (2**retries))
                    retries += 1
                    continue
                self.failures += 1
                turn.incomplete = True
                if error.http_status != 429:
                    card.writable = False
                self._report_degraded(turn.session_id, turn.turn_id)
                return False
            except Exception:
                self.failures += 1
                card.writable = False
                turn.incomplete = True
                self._report_degraded(turn.session_id, turn.turn_id)
                _LOGGER.exception("Lark activity card update failed")
                return False
            self.elements_updated += 1
            card.next_sequence = sequence + 1
            return True

    async def _create_card(
        self,
        turn: _ActivityTurn,
        content: dict[str, object],
    ) -> bool:
        card = {
            "schema": "2.0",
            "config": {"update_multi": True},
            "body": {"elements": [content]},
        }
        reply_uuid = uuid4().hex
        try:
            card_id = await self._retry_create(
                lambda: turn.api.create_card(card, timeout=_REQUEST_TIMEOUT_SECONDS)
            )
            provider_message_id = await self._retry_create(
                lambda: turn.api.reply_card(
                    message_id=turn.route.message_id,
                    card_id=card_id,
                    reply_in_thread=turn.route.reply_in_thread,
                    uuid=reply_uuid,
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failures += 1
            turn.incomplete = True
            self._report_degraded(turn.session_id, turn.turn_id)
            _LOGGER.exception("Lark activity card creation failed")
            return False
        turn.card = CardState(
            card_id=card_id,
            provider_message_id=provider_message_id,
            last_request_at=monotonic(),
        )
        self.cards_created += 1
        return True

    async def _retry_create(self, request: Callable[[], Awaitable[str]]) -> str:
        retries = 0
        while True:
            try:
                await self._rate_limit(None)
                return await request()
            except LarkApiError as error:
                if error.http_status != 429 or retries >= _MAX_RATE_LIMIT_RETRIES:
                    raise
                self.rate_limit_retries += 1
                await self._wait_ms(100 * (2**retries))
                retries += 1

    async def _rate_limit(self, card: CardState | None) -> None:
        while True:
            async with self._rate_lock:
                now = monotonic()
                while self._requests_second and self._requests_second[0] <= now - 1:
                    self._requests_second.pop(0)
                while self._requests_minute and self._requests_minute[0] <= now - 60:
                    self._requests_minute.pop(0)
                wait = 0.0
                if card is not None:
                    wait = max(
                        0.0,
                        card.last_request_at + _CARD_REQUEST_INTERVAL_SECONDS - now,
                    )
                if len(self._requests_second) >= 50:
                    wait = max(wait, self._requests_second[0] + 1 - now)
                if len(self._requests_minute) >= 1000:
                    wait = max(wait, self._requests_minute[0] + 60 - now)
                if wait <= 0:
                    if card is not None:
                        card.last_request_at = now
                    self._requests_second.append(now)
                    self._requests_minute.append(now)
                    return
            await self._wait_ms(max(1, int(wait * 1000)))

    def _finish_turn(self, turn: _ActivityTurn) -> None:
        if turn.incomplete:
            self._report_degraded(turn.session_id, turn.turn_id)

    async def _wait_ms(self, delay_ms: int) -> None:
        timer = self._timer_wheel.create(
            min(max(1, delay_ms), self._timer_wheel.maximum_delay_ms)
        )
        await timer.wait()

    def _render(self, turn: _ActivityTurn) -> dict[str, object] | None:
        overview = turn.reducer.overview
        if overview is not None:
            if overview.empty:
                return turn.desired
            return self._container(
                [
                    self._title_element(overview.outcome),
                    *self._overview_elements(overview),
                ]
            )
        snapshot = turn.reducer.snapshot
        if snapshot is None:
            return None
        line = snapshot_line(self._translator, snapshot)
        content = _ACTIVITY_TEMPLATE.render(
            {
                "label": line.label,
                "name": _truncate_utf8(line.name, _MAX_TOOL_NAME_BYTES)
                if line.name
                else "",
            }
        ).strip()
        return self._container(
            [
                self._title_element(ActivityOutcome.RUNNING),
                self._line_element(content, snapshot.kind),
            ]
        )

    @staticmethod
    def _line_element(content: str, kind: ActivityKind) -> dict[str, object]:
        return {
            "tag": "markdown",
            "text_size": "normal",
            "content": content,
            "icon": {"tag": "standard_icon", "token": _KIND_ICONS[kind]},
        }

    def _title_element(self, outcome: ActivityOutcome) -> dict[str, object]:
        return {
            "tag": "markdown",
            "text_size": "normal",
            "content": _ACTIVITY_TITLE_TEMPLATE.render(
                {
                    "title": self._title,
                    "color": _OUTCOME_COLORS[outcome],
                    "state": self._translator.text(f"activity.state.{outcome.value}"),
                }
            ).strip(),
        }

    @staticmethod
    def _container(elements: list[dict[str, object]]) -> dict[str, object]:
        return {
            "tag": "column_set",
            "element_id": _ACTIVITY_ELEMENT_ID,
            "flex_mode": "none",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "direction": "vertical",
                    "vertical_spacing": "small",
                    "elements": elements,
                }
            ],
        }

    def _overview_elements(self, overview: ActivityOverview) -> list[dict[str, object]]:
        elements: list[dict[str, object]] = []
        if overview.error_message:
            elements.append(
                {
                    "tag": "markdown",
                    "text_size": "normal",
                    "content": self._translator.text(
                        "activity.error", {"error": overview.error_message}
                    ),
                }
            )
        counts = (
            (
                ActivityKind.TOOL_CALL,
                "activity.count.tool_calls",
                overview.tool_calls,
            ),
            (
                ActivityKind.CONTEXT_COMPACTION,
                "activity.count.context_compactions",
                overview.context_compactions,
            ),
        )
        for kind, key, count in counts:
            if not count:
                continue
            elements.append(
                self._line_element(
                    self._translator.text(key, {"count": count}),
                    kind,
                )
            )
        tokens = [
            ("activity.label.input", overview.input_tokens),
            ("activity.label.cached", overview.cached_input_tokens),
            ("activity.label.output", overview.output_tokens),
        ]
        if any(value for _, value in tokens):
            if elements:
                elements.append({"tag": "hr"})
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "trisect",
                    "horizontal_spacing": "medium",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "direction": "vertical",
                            "vertical_spacing": "small",
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "text_size": "notation",
                                    "content": self._translator.text(key),
                                },
                                {
                                    "tag": "markdown",
                                    "text_size": "heading",
                                    "content": f"{value}",
                                },
                            ],
                        }
                        for key, value in tokens
                        if value
                    ],
                }
            )
            elements.append({"tag": "hr"})
            elements.append(
                {
                    "tag": "markdown",
                    "text_size": "notation",
                    "content": self._translator.text("activity.note.tokens"),
                }
            )
        return elements

    @staticmethod
    def _turn_key(item: RuntimeOutputEvent) -> tuple[str, str] | None:
        turn_id = item.envelope.turn_id or item.envelope.provider_turn_id
        if turn_id is None:
            return None
        return item.envelope.session_id, turn_id


def _truncate_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    suffix = "…"
    prefix = encoded[: limit - len(suffix.encode("utf-8"))]
    return prefix.decode("utf-8", errors="ignore") + suffix


__all__ = [
    "CardState",
    "LarkActivityProjector",
    "LarkActivityRoute",
]
