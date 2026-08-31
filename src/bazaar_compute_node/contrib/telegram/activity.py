from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from ...core.models import (
    ContextCompactionCompleted,
    ContextCompactionStarted,
    RuntimeOutputEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallInteraction,
    ToolCallPatchUpdated,
    ToolCallStarted,
    ToolCallTextDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnUnknown,
)
from ...core.timerwheel import TimerWheel
from ...i18n import Translator
from ...rendering import TextTemplate
from .api import TelegramApiError, TelegramBotApi
from .identity import TelegramThreadIdentity

MAX_RICH_MARKDOWN_BYTES = 32_768
_EDIT_DEBOUNCE_MS = 1000
_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_RATE_LIMIT_RETRIES = 3
_MAX_TOOL_NAME_BYTES = 64
_MAX_RENDERED_ROW_BYTES = 256
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")
_LOGGER = logging.getLogger(__name__)
_ACTIVITY_TEMPLATE = TextTemplate.from_resource("telegram_activity.tpl")


@dataclass(slots=True)
class _ActivityRow:
    kind: str
    name: str
    page_index: int
    status: str = "running"


@dataclass(slots=True)
class _ActivityPage:
    row_ids: list[str] = field(default_factory=list)
    message_id: int | None = None


@dataclass(slots=True)
class _ActivityTurn:
    identity: TelegramThreadIdentity
    queue: asyncio.Queue[RuntimeOutputEvent] = field(default_factory=asyncio.Queue)
    rows: dict[str, _ActivityRow] = field(default_factory=dict)
    pages: list[_ActivityPage] = field(default_factory=list)
    dirty_pages: set[int] = field(default_factory=set)


class TelegramActivityProjector:
    def __init__(
        self,
        *,
        timer_wheel: TimerWheel | None,
        translator: Translator,
    ) -> None:
        self._timer_wheel = timer_wheel
        self._title = translator.text("activity.title")
        self._tool_call = translator.text("activity.kind.tool_call")
        self._context_compaction = translator.text("activity.kind.context_compaction")
        self._turns: dict[tuple[str, str], _ActivityTurn] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self.messages_sent = 0
        self.messages_edited = 0
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
        identity: TelegramThreadIdentity | None,
        api: TelegramBotApi | None,
    ) -> None:
        key = self._turn_key(item)
        if key is None:
            return
        turn = self._turns.get(key)
        if turn is None:
            if not isinstance(
                item.payload,
                ToolCallStarted | ContextCompactionStarted | ContextCompactionCompleted,
            ):
                return
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
        first = True
        try:
            while True:
                item = await turn.queue.get()
                terminal = self._apply(turn, item)
                if first:
                    first = False
                elif not terminal:
                    terminal = await self._collect_debounced(turn)
                await self._flush(turn, api)
                if terminal:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failures += 1
            _LOGGER.exception("Telegram tool activity projector failed")
        finally:
            if self._turns.get(key) is turn:
                self._turns.pop(key, None)

    async def _collect_debounced(self, turn: _ActivityTurn) -> bool:
        deadline = asyncio.get_running_loop().time() + _EDIT_DEBOUNCE_MS / 1000
        terminal = False
        while not terminal:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(turn.queue.get(), timeout=remaining)
            except TimeoutError:
                break
            terminal = self._apply(turn, item)
        return terminal

    def _apply(self, turn: _ActivityTurn, item: RuntimeOutputEvent) -> bool:
        match item.payload:
            case ToolCallStarted(call=call):
                row_id = f"tool:{call.call_id}"
                row = turn.rows.get(row_id)
                if row is None:
                    row = _ActivityRow(
                        kind=self._tool_call,
                        name=call.name,
                        page_index=self._page_for_new_row(turn, row_id),
                    )
                    turn.rows[row_id] = row
                else:
                    row.name = call.name
                    row.status = "running"
                turn.dirty_pages.add(row.page_index)
            case ToolCallCompleted(call=call):
                row = turn.rows.get(f"tool:{call.call_id}")
                if row is not None:
                    row.name = call.name
                    row.status = "completed"
                    turn.dirty_pages.add(row.page_index)
            case ToolCallFailed(call=call):
                row = turn.rows.get(f"tool:{call.call_id}")
                if row is not None:
                    row.name = call.name
                    row.status = "failed"
                    turn.dirty_pages.add(row.page_index)
            case ToolCallTextDelta() | ToolCallPatchUpdated() | ToolCallInteraction():
                pass
            case (
                ContextCompactionStarted(compaction_id=compaction_id)
                | ContextCompactionCompleted(compaction_id=compaction_id)
            ) as payload:
                row_id = f"compaction:{compaction_id or 'current'}"
                row = turn.rows.get(row_id)
                if row is None:
                    row = _ActivityRow(
                        kind=self._context_compaction,
                        name="",
                        page_index=self._page_for_new_row(turn, row_id),
                    )
                    turn.rows[row_id] = row
                row.status = (
                    "running"
                    if isinstance(payload, ContextCompactionStarted)
                    else "completed"
                )
                turn.dirty_pages.add(row.page_index)
            case (
                TurnCompleted(event_name=event_name)
                | TurnFailed(event_name=event_name)
                | TurnCancelled(event_name=event_name)
                | TurnUnknown(event_name=event_name)
            ):
                return "turn" in event_name.casefold()
            case _:
                pass
        return False

    def _page_for_new_row(
        self,
        turn: _ActivityTurn,
        row_id: str,
    ) -> int:
        if not turn.pages:
            turn.pages.append(_ActivityPage())
        page_index = len(turn.pages) - 1
        page = turn.pages[page_index]
        reserved = (
            len(self._title.encode("utf-8"))
            + (len(page.row_ids) + 1) * _MAX_RENDERED_ROW_BYTES
        )
        if reserved <= MAX_RICH_MARKDOWN_BYTES:
            page.row_ids.append(row_id)
            return page_index
        page_index += 1
        turn.pages.append(_ActivityPage(row_ids=[row_id]))
        return page_index

    async def _flush(
        self,
        turn: _ActivityTurn,
        api: TelegramBotApi,
    ) -> None:
        dirty_pages = sorted(turn.dirty_pages)
        turn.dirty_pages.clear()
        for position, page_index in enumerate(dirty_pages):
            page = turn.pages[page_index]
            markdown = self._render_page(turn, page)
            payload: dict[str, object] = {
                "chat_id": turn.identity.chat_id,
                "rich_message": {
                    "markdown": markdown,
                    "skip_entity_detection": True,
                },
            }
            try:
                if page.message_id is None:
                    if turn.identity.topic_id:
                        payload["message_thread_id"] = turn.identity.topic_id
                    result = await self._request_with_retry(
                        lambda timeout, payload=payload: api.send_rich_message(
                            payload, timeout=timeout
                        )
                    )
                    message_id = result.get("message_id")
                    if (
                        not isinstance(message_id, int)
                        or isinstance(message_id, bool)
                        or message_id <= 0
                    ):
                        raise ValueError("Telegram activity message has no message_id")
                    page.message_id = message_id
                    self.messages_sent += 1
                else:
                    payload["message_id"] = page.message_id
                    await self._request_with_retry(
                        lambda timeout, payload=payload: api.edit_message_text(
                            payload, timeout=timeout
                        )
                    )
                    self.messages_edited += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failures += 1
                turn.dirty_pages.update(dirty_pages[position:])
                _LOGGER.exception("Telegram tool activity message update failed")
                return

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

    def _render_page(self, turn: _ActivityTurn, page: _ActivityPage) -> str:
        rows: list[dict[str, str]] = []
        for row_id in page.row_ids:
            row = turn.rows[row_id]
            rows.append(
                {
                    "icon": {
                        "running": "⌛️",
                        "completed": "✅",
                        "failed": "❌",
                    }[row.status],
                    "kind": _escape_markdown(row.kind),
                    "name": _escape_markdown(
                        _truncate_utf8(row.name, _MAX_TOOL_NAME_BYTES)
                    ),
                }
            )
        return _ACTIVITY_TEMPLATE.render(
            {
                "title": _escape_markdown(self._title),
                "rows": rows,
            }
        )

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
