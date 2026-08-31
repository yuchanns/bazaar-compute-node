from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from uuid import uuid4

from ...core.models import (
    ContentDelta,
    ContextCompactionCompleted,
    ContextCompactionStarted,
    RuntimeOutputEvent,
    ToolCallCompleted,
    ToolCallDeltaKind,
    ToolCallFailed,
    ToolCallInteraction,
    ToolCallPatchUpdated,
    ToolCallStarted,
    ToolCallTextDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    TurnUnknown,
    UsageUpdated,
)
from ...core.sanitization import redact_sensitive_text, redact_sensitive_value
from ...core.timerwheel import TimerWheel
from ...i18n import Translator
from .api import LarkApi, LarkApiError

_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_RATE_LIMIT_RETRIES = 3
_MAX_PENDING_EVENTS = 1024
_MAX_TOOL_NAME_BYTES = 64
_MAX_INPUT_BYTES = 192
_MAX_OUTPUT_BYTES = 192
_MAX_SUMMARY_SOURCE_BYTES = 2048
_MAX_ROW_BYTES = 512
_FIXED_CARD_BYTES = 4096
_FIXED_CARD_ELEMENTS = 20
_MAX_CARD_BYTES = 30 * 1024
_MAX_CARD_ELEMENTS = 200
_MAX_ROWS_PER_CARD = 51
_CARD_REQUEST_INTERVAL_SECONDS = 0.1
_LOGGER = logging.getLogger(__name__)
_CARD_STOP = object()


@dataclass(frozen=True, slots=True)
class LarkActivityRoute:
    message_id: str
    reply_in_thread: bool


@dataclass(slots=True)
class CardState:
    card_id: str
    provider_message_id: str
    ordinal: int
    next_sequence: int = 1
    last_success_sequence: int = 0
    used_elements: int = _FIXED_CARD_ELEMENTS
    used_bytes: int = _FIXED_CARD_BYTES
    queue: asyncio.Queue[_CardOperation | object] = field(default_factory=asyncio.Queue)
    writer: asyncio.Task[None] | None = None
    last_request_at: float = 0.0
    writable: bool = True
    incomplete: bool = False


@dataclass(slots=True)
class _ActivityRow:
    card: CardState
    element_id: str
    name: str
    status: str = "running"
    input_text: str = ""
    output_text: str = ""


@dataclass(frozen=True, slots=True)
class _CardOperation:
    uuid: str
    element_id: str | None
    element: dict[str, object]


@dataclass(slots=True)
class _ActivityTurn:
    session_id: str
    route: LarkActivityRoute
    api: LarkApi
    pending: deque[RuntimeOutputEvent] = field(default_factory=deque)
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    rows: dict[str, _ActivityRow] = field(default_factory=dict)
    cards: list[CardState] = field(default_factory=list)
    buffered_compactions: list[RuntimeOutputEvent] = field(default_factory=list)
    next_element: int = 1
    incomplete: bool = False
    terminal: asyncio.Event = field(default_factory=asyncio.Event)


class LarkActivityProjector:
    def __init__(
        self,
        *,
        timer_wheel: TimerWheel,
        translator: Translator,
        report_degraded: Callable[[str], None],
    ) -> None:
        self._timer_wheel = timer_wheel
        self._report_degraded = report_degraded
        self._title = translator.text("activity.title")
        self._context_compaction = translator.text("activity.context_compaction")
        self._input_label = translator.text("activity.label.input")
        self._output_label = translator.text("activity.label.output")
        self._incomplete = translator.text("activity.incomplete")
        self._continued = translator.text("activity.continued")
        self._statuses = {
            "running": translator.text("activity.status.running"),
            "completed": translator.text("activity.status.completed"),
            "failed": translator.text("activity.status.failed"),
        }
        self._turns: dict[tuple[str, str], _ActivityTurn] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._rate_lock = asyncio.Lock()
        self._requests_second: deque[float] = deque()
        self._requests_minute: deque[float] = deque()
        self.cards_created = 0
        self.elements_added = 0
        self.elements_updated = 0
        self.failures = 0
        self.rate_limit_retries = 0

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
        key = self._turn_key(item)
        if key is None:
            return
        turn = self._turns.get(key)
        if turn is None:
            if not isinstance(
                item.payload,
                ToolCallStarted
                | ToolCallCompleted
                | ToolCallFailed
                | ContextCompactionStarted
                | ContextCompactionCompleted,
            ):
                return
            if route is None or api is None:
                return
            turn = _ActivityTurn(
                session_id=item.envelope.session_id,
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
        if len(turn.pending) >= _MAX_PENDING_EVENTS:
            self.failures += 1
            turn.incomplete = True
            if self._is_terminal(item):
                turn.pending.popleft()
            else:
                return
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
                item = turn.pending.popleft()
                if await self._apply(turn, item):
                    await self._finish_turn(turn)
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failures += 1
            self._report_degraded(turn.session_id)
            _LOGGER.exception("Lark activity projector failed")
        finally:
            await self._stop_card_writers(turn)
            if self._turns.get(key) is turn:
                self._turns.pop(key, None)
            turn.terminal.set()

    async def _apply(
        self,
        turn: _ActivityTurn,
        item: RuntimeOutputEvent,
    ) -> bool:
        match item.payload:
            case ToolCallStarted(call=call):
                if not turn.cards and not await self._create_card(turn):
                    return False
                await self._project_buffered_compactions(turn)
                row = turn.rows.get(f"tool:{call.call_id}")
                if row is None:
                    row = await self._new_row(
                        turn,
                        f"tool:{call.call_id}",
                        call.name,
                        input_text=_summary_source(call.input),
                    )
                else:
                    row.name = call.name
                    row.status = "running"
                    if call.input is not None:
                        row.input_text = _summary_source(call.input)
                    row.output_text = ""
                    self._queue_update(row)
            case ToolCallCompleted(call=call):
                row = turn.rows.get(f"tool:{call.call_id}")
                if row is None:
                    await self._tool_row(
                        turn,
                        call.call_id,
                        call.name,
                        call.input,
                        status="completed",
                        output_text=_summary_source(call.output),
                    )
                else:
                    row.name = call.name
                    row.status = "completed"
                    if call.output is not None:
                        row.output_text = _summary_source(call.output)
                    self._queue_update(row)
            case ToolCallFailed(call=call, error_message=error_message):
                output = call.output if call.output is not None else error_message
                row = turn.rows.get(f"tool:{call.call_id}")
                if row is None:
                    await self._tool_row(
                        turn,
                        call.call_id,
                        call.name,
                        call.input,
                        status="failed",
                        output_text=_summary_source(output),
                    )
                else:
                    row.name = call.name
                    row.status = "failed"
                    if output is not None:
                        row.output_text = _summary_source(output)
                    self._queue_update(row)
            case ToolCallTextDelta(call_id=call_id, kind=kind, text=text):
                row = turn.rows.get(f"tool:{call_id}")
                if row is not None:
                    if kind is ToolCallDeltaKind.INPUT:
                        row.input_text = _append_source(row.input_text, text)
                    else:
                        row.output_text = _append_source(row.output_text, text)
                    self._queue_update(row)
            case ToolCallPatchUpdated(call_id=call_id, changes=changes):
                row = turn.rows.get(f"tool:{call_id}")
                if row is not None:
                    row.output_text = _summary_source(
                        [
                            {"path": change.path, "kind": change.kind}
                            for change in changes
                        ]
                    )
                    self._queue_update(row)
            case ToolCallInteraction(call_id=call_id, stdin=stdin):
                row = turn.rows.get(f"tool:{call_id}")
                if row is not None:
                    row.input_text = _append_source(row.input_text, stdin)
                    self._queue_update(row)
            case ContextCompactionStarted() | ContextCompactionCompleted():
                if turn.cards:
                    await self._project_compaction(turn, item)
                else:
                    turn.buffered_compactions.append(item)
            case (
                TurnCompleted(event_name=event_name)
                | TurnFailed(event_name=event_name)
                | TurnCancelled(event_name=event_name)
                | TurnUnknown(event_name=event_name)
            ):
                return "turn" in event_name.casefold()
            case TurnStarted() | ContentDelta() | UsageUpdated():
                pass
        return False

    async def _tool_row(
        self,
        turn: _ActivityTurn,
        call_id: str,
        name: str,
        input_value: object,
        *,
        status: str,
        output_text: str,
    ) -> _ActivityRow | None:
        row = turn.rows.get(f"tool:{call_id}")
        if row is not None:
            return row
        if not turn.cards and not await self._create_card(turn):
            return None
        await self._project_buffered_compactions(turn)
        return await self._new_row(
            turn,
            f"tool:{call_id}",
            name,
            status=status,
            input_text=_summary_source(input_value),
            output_text=output_text,
        )

    async def _project_buffered_compactions(self, turn: _ActivityTurn) -> None:
        buffered = tuple(turn.buffered_compactions)
        turn.buffered_compactions.clear()
        for item in buffered:
            await self._project_compaction(turn, item)

    async def _project_compaction(
        self,
        turn: _ActivityTurn,
        item: RuntimeOutputEvent,
    ) -> None:
        payload = item.payload
        if isinstance(payload, ContextCompactionStarted | ContextCompactionCompleted):
            row_id = f"compaction:{payload.compaction_id or 'current'}"
            row = turn.rows.get(row_id)
            if row is None:
                await self._new_row(
                    turn,
                    row_id,
                    self._context_compaction,
                    status=(
                        "running"
                        if isinstance(payload, ContextCompactionStarted)
                        else "completed"
                    ),
                )
            else:
                row.status = (
                    "running"
                    if isinstance(payload, ContextCompactionStarted)
                    else "completed"
                )
                self._queue_update(row)

    async def _new_row(
        self,
        turn: _ActivityTurn,
        row_id: str,
        name: str,
        *,
        status: str = "running",
        input_text: str = "",
        output_text: str = "",
    ) -> _ActivityRow | None:
        card = turn.cards[-1]
        if (
            card.used_elements - _FIXED_CARD_ELEMENTS >= _MAX_ROWS_PER_CARD
            or card.used_bytes + _MAX_ROW_BYTES > _MAX_CARD_BYTES
            or card.used_elements + 1 > _MAX_CARD_ELEMENTS
        ):
            self._queue_add(
                card,
                {
                    "tag": "markdown",
                    "content": self._continued,
                },
            )
            if not await self._create_card(turn):
                return None
            card = turn.cards[-1]
        element_id = f"i{turn.next_element:06d}"
        turn.next_element += 1
        row = _ActivityRow(
            card=card,
            element_id=element_id,
            name=name,
            status=status,
            input_text=input_text,
            output_text=output_text,
        )
        turn.rows[row_id] = row
        card.used_elements += 1
        card.used_bytes += _MAX_ROW_BYTES
        self._queue_add(card, self._row_element(row))
        return row

    async def _create_card(self, turn: _ActivityTurn) -> bool:
        ordinal = len(turn.cards) + 1
        title = self._title if ordinal == 1 else f"{self._title} · {ordinal}"
        card = {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {"elements": []},
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
            self._report_degraded(turn.session_id)
            _LOGGER.exception("Lark activity card creation failed")
            return False
        state = CardState(
            card_id=card_id,
            provider_message_id=provider_message_id,
            ordinal=ordinal,
        )
        turn.cards.append(state)
        state.writer = asyncio.create_task(
            self._run_card_writer(turn, state),
            name=f"bcn-lark-card-{card_id}",
        )
        self._tasks.add(state.writer)
        state.writer.add_done_callback(self._tasks.discard)
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

    def _queue_add(self, card: CardState, element: dict[str, object]) -> None:
        card.queue.put_nowait(
            _CardOperation(uuid=uuid4().hex, element_id=None, element=element)
        )

    def _queue_update(self, row: _ActivityRow) -> None:
        row.card.queue.put_nowait(
            _CardOperation(
                uuid=uuid4().hex,
                element_id=row.element_id,
                element=self._row_element(row),
            )
        )

    async def _run_card_writer(
        self,
        turn: _ActivityTurn,
        card: CardState,
    ) -> None:
        while True:
            operation = await card.queue.get()
            try:
                if operation is _CARD_STOP:
                    return
                if not isinstance(operation, _CardOperation) or not card.writable:
                    continue
                await self._write_operation(turn, card, operation)
            finally:
                card.queue.task_done()

    async def _write_operation(
        self,
        turn: _ActivityTurn,
        card: CardState,
        operation: _CardOperation,
    ) -> None:
        sequence = card.next_sequence
        retries = 0
        while True:
            try:
                await self._rate_limit(card)
                if operation.element_id is None:
                    await turn.api.add_card_elements(
                        card.card_id,
                        [operation.element],
                        uuid=operation.uuid,
                        sequence=sequence,
                        timeout=_REQUEST_TIMEOUT_SECONDS,
                    )
                    self.elements_added += 1
                else:
                    await turn.api.update_card_element(
                        card.card_id,
                        operation.element_id,
                        operation.element,
                        uuid=operation.uuid,
                        sequence=sequence,
                        timeout=_REQUEST_TIMEOUT_SECONDS,
                    )
                    self.elements_updated += 1
                card.last_success_sequence = sequence
                card.next_sequence = sequence + 1
                return
            except asyncio.CancelledError:
                raise
            except LarkApiError as error:
                if error.http_status == 429 and retries < _MAX_RATE_LIMIT_RETRIES:
                    self.rate_limit_retries += 1
                    await self._wait_ms(100 * (2**retries))
                    retries += 1
                    continue
                self.failures += 1
                card.incomplete = True
                turn.incomplete = True
                if error.http_status != 429:
                    card.writable = False
                    self._report_degraded(turn.session_id)
                return
            except Exception:
                self.failures += 1
                card.incomplete = True
                card.writable = False
                turn.incomplete = True
                self._report_degraded(turn.session_id)
                _LOGGER.exception("Lark activity card update failed")
                return

    async def _rate_limit(self, card: CardState | None) -> None:
        while True:
            async with self._rate_lock:
                now = monotonic()
                while self._requests_second and self._requests_second[0] <= now - 1:
                    self._requests_second.popleft()
                while self._requests_minute and self._requests_minute[0] <= now - 60:
                    self._requests_minute.popleft()
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

    async def _finish_turn(self, turn: _ActivityTurn) -> None:
        await asyncio.gather(*(card.queue.join() for card in turn.cards))
        if turn.incomplete:
            if not turn.cards:
                self._report_degraded(turn.session_id)
                return
            for card in turn.cards:
                if card.writable:
                    self._queue_add(
                        card,
                        {"tag": "markdown", "content": self._incomplete},
                    )
            await asyncio.gather(*(card.queue.join() for card in turn.cards))

    async def _stop_card_writers(self, turn: _ActivityTurn) -> None:
        writers = []
        for card in turn.cards:
            writer = card.writer
            if writer is not None and not writer.done():
                card.queue.put_nowait(_CARD_STOP)
                writers.append(writer)
        if writers:
            await asyncio.gather(*writers, return_exceptions=True)

    async def _wait_ms(self, delay_ms: int) -> None:
        timer = self._timer_wheel.create(
            min(max(1, delay_ms), self._timer_wheel.maximum_delay_ms)
        )
        await timer.wait()

    def _row_element(self, row: _ActivityRow) -> dict[str, object]:
        icon = {
            "running": "⏳",
            "completed": "✅",
            "failed": "❌",
        }[row.status]
        name = _truncate_utf8(row.name, _MAX_TOOL_NAME_BYTES)
        parts = [f"{icon} **{name}** · {self._statuses[row.status]}"]
        input_text = _truncate_utf8(
            redact_sensitive_text(row.input_text), _MAX_INPUT_BYTES
        )
        output_text = _truncate_utf8(
            redact_sensitive_text(row.output_text), _MAX_OUTPUT_BYTES
        )
        if input_text:
            parts.append(f"**{self._input_label}:** `{input_text}`")
        if output_text:
            parts.append(f"**{self._output_label}:** `{output_text}`")
        return {
            "tag": "markdown",
            "element_id": row.element_id,
            "content": "\n".join(parts),
        }

    @staticmethod
    def _turn_key(item: RuntimeOutputEvent) -> tuple[str, str] | None:
        turn_id = item.envelope.turn_id or item.envelope.provider_turn_id
        if turn_id is None:
            return None
        return item.envelope.session_id, turn_id

    @staticmethod
    def _is_terminal(item: RuntimeOutputEvent) -> bool:
        payload = item.payload
        return (
            isinstance(
                payload,
                TurnCompleted | TurnFailed | TurnCancelled | TurnUnknown,
            )
            and "turn" in payload.event_name.casefold()
        )


def _summary_source(value: object) -> str:
    if value is None:
        return ""
    value = redact_sensitive_value(value)
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _truncate_utf8(text, _MAX_SUMMARY_SOURCE_BYTES)


def _append_source(current: str, text: str) -> str:
    return _truncate_utf8(current + text, _MAX_SUMMARY_SOURCE_BYTES)


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
