from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from time import monotonic

from ...core.activity import (
    ActivityOutcome,
    ActivityReducer,
    overview_lines,
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
from .api import TelegramApiError, TelegramBotApi
from .identity import TelegramThreadIdentity

MAX_RICH_MARKDOWN_BYTES = 32_768
_WRITE_INTERVAL_SECONDS = 1.0
_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_RATE_LIMIT_RETRIES = 3
_MAX_TOOL_NAME_BYTES = 64
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")
_LOGGER = logging.getLogger(__name__)
_ACTIVITY_TEMPLATE = TextTemplate.from_resource("telegram_activity.tpl")


@dataclass(slots=True)
class _ActivityTurn:
    identity: TelegramThreadIdentity
    reducer: ActivityReducer = field(default_factory=ActivityReducer)
    queue: asyncio.Queue[RuntimeOutputEvent] = field(default_factory=asyncio.Queue)
    message_id: int | None = None
    dirty: bool = False
    written: str | None = None
    last_write_at: float | None = None


class TelegramActivityProjector:
    def __init__(
        self,
        *,
        timer_wheel: TimerWheel | None,
        translator: Translator,
    ) -> None:
        self._timer_wheel = timer_wheel
        self._translator = translator
        self._title = translator.text("activity.title")
        self._turns: dict[tuple[str, str], _ActivityTurn] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self.messages_sent = 0
        self.messages_edited = 0
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
        identity: TelegramThreadIdentity | None,
        api: TelegramBotApi | None,
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
            if identity is None or api is None:
                return
            turn = _ActivityTurn(identity=identity)
            self._turns[key] = turn
            task = asyncio.create_task(
                self._run_turn(key, turn, api),
                name=f"bcn-telegram-activity-{key[1]}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        turn.queue.put_nowait(item)

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
        api: TelegramBotApi,
    ) -> None:
        try:
            while True:
                item = await turn.queue.get()
                if turn.reducer.apply(item.payload):
                    turn.dirty = True
                if turn.reducer.overview is not None:
                    await self._wait_for_interval(turn, coalesce=False)
                    await self._write(turn, api)
                    return
                if not turn.dirty:
                    continue
                await self._wait_for_interval(turn, coalesce=True)
                await self._write(turn, api)
                if turn.reducer.overview is not None:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failures += 1
            _LOGGER.exception("Telegram activity projector failed")
        finally:
            if self._turns.get(key) is turn:
                self._turns.pop(key, None)

    async def _wait_for_interval(self, turn: _ActivityTurn, *, coalesce: bool) -> None:
        while True:
            last_write_at = turn.last_write_at
            if last_write_at is None:
                return
            remaining = last_write_at + _WRITE_INTERVAL_SECONDS - monotonic()
            if remaining <= 0:
                return
            if not coalesce:
                await asyncio.sleep(remaining)
                return
            try:
                item = await asyncio.wait_for(turn.queue.get(), timeout=remaining)
            except TimeoutError:
                return
            self.coalesced_updates += 1
            if turn.reducer.apply(item.payload):
                turn.dirty = True

    async def _write(self, turn: _ActivityTurn, api: TelegramBotApi) -> None:
        markdown = self._render(turn)
        if markdown is None:
            return
        if markdown == turn.written:
            turn.dirty = False
            return
        payload: dict[str, object] = {
            "chat_id": turn.identity.chat_id,
            "rich_message": {
                "markdown": markdown,
                "skip_entity_detection": True,
            },
        }
        try:
            if turn.message_id is None:
                if turn.identity.topic_id:
                    payload["message_thread_id"] = turn.identity.topic_id
                result = await self._request_with_retry(
                    lambda timeout: api.send_rich_message(payload, timeout=timeout)
                )
                message_id = result.get("message_id")
                if (
                    not isinstance(message_id, int)
                    or isinstance(message_id, bool)
                    or message_id <= 0
                ):
                    raise ValueError("Telegram activity message has no message_id")
                turn.message_id = message_id
                self.messages_sent += 1
            else:
                payload["message_id"] = turn.message_id
                await self._request_with_retry(
                    lambda timeout: api.edit_message_text(payload, timeout=timeout)
                )
                self.messages_edited += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failures += 1
            _LOGGER.exception("Telegram activity message update failed")
            return
        turn.dirty = False
        turn.written = markdown
        turn.last_write_at = monotonic()

    def _render(self, turn: _ActivityTurn) -> str | None:
        overview = turn.reducer.overview
        if overview is not None:
            if overview.empty:
                return None
            if overview.error_message:
                overview = replace(
                    overview,
                    error_message=_escape_markdown(overview.error_message),
                )
            has_tokens = bool(
                overview.input_tokens
                or overview.cached_input_tokens
                or overview.output_tokens
            )
            return _ACTIVITY_TEMPLATE.render(
                {
                    "title": _escape_markdown(self._title),
                    "state": self._state_text(overview.outcome),
                    "line": None,
                    "overview": list(overview_lines(self._translator, overview)),
                    "note": _escape_markdown(
                        self._translator.text("activity.note.tokens")
                    )
                    if has_tokens
                    else "",
                }
            )
        snapshot = turn.reducer.snapshot
        if snapshot is None:
            return None
        line = snapshot_line(self._translator, snapshot)
        return _ACTIVITY_TEMPLATE.render(
            {
                "title": _escape_markdown(self._title),
                "state": self._state_text(ActivityOutcome.RUNNING),
                "note": "",
                "line": {
                    "icon": line.icon,
                    "label": _escape_markdown(line.label),
                    "name": _escape_markdown(
                        _truncate_utf8(line.name, _MAX_TOOL_NAME_BYTES)
                    )
                    if line.name
                    else "",
                },
                "overview": [],
            }
        )

    def _state_text(self, outcome: ActivityOutcome) -> str:
        return _escape_markdown(
            self._translator.text(f"activity.state.{outcome.value}")
        )

    async def _request_with_retry(
        self,
        request: Callable[[float], Awaitable[Mapping[str, object]]],
    ) -> Mapping[str, object]:
        retries = 0
        while True:
            try:
                return await request(_REQUEST_TIMEOUT_SECONDS)
            except TelegramApiError as error:
                retry_after = error.retry_after
                if (
                    retry_after is None
                    or retry_after < 0
                    or retries >= _MAX_RATE_LIMIT_RETRIES
                ):
                    raise
                timer_wheel = self._timer_wheel
                if timer_wheel is None:
                    raise RuntimeError(
                        "Telegram activity retry requires a timer wheel"
                    ) from error
                self.rate_limit_retries += 1
                retries += 1
                timer = timer_wheel.create(math.ceil(float(retry_after) * 1000))
                await timer.wait()

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


def _escape_markdown(value: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", value)


__all__ = ["MAX_RICH_MARKDOWN_BYTES", "TelegramActivityProjector"]
