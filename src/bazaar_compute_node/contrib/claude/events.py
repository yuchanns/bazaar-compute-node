from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from time import time_ns
from typing import Self, cast

from ...core.models import (
    ContentDelta,
    ContentDeltaKind,
    JsonValue,
    RuntimeEventEnvelope,
    RuntimeEventPayload,
    RuntimeEventState,
    RuntimeOutputEvent,
    ToolCallDeltaKind,
    ToolCallTextDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    TurnUnknown,
)
from ...core.runtime import IRuntimeTurnStream
from .client import TurnInbox
from .protocol import ClaudeProtocolError, ClaudeTransportError, JsonObject

ResultClaim = Callable[[JsonObject], Awaitable[bool]]
ClosedHandler = Callable[[], Awaitable[None]]
UnusableHandler = Callable[[BaseException], Awaitable[None]]
_LOGGER = logging.getLogger("bazaar_compute_node.runtime.claudecode")


class TurnEventStream(IRuntimeTurnStream):
    """Normalize one persistent Claude stream into provider-neutral turn events."""

    def __init__(
        self,
        inbox: TurnInbox,
        *,
        session_id: str,
        runtime_session_id: str,
        turn_id: str,
        provider_thread_id: str,
        claude_version: tuple[int, int, int],
        claim_result: ResultClaim,
        on_closed: ClosedHandler,
        on_unusable: UnusableHandler,
        initial_error: BaseException | None = None,
        initial_error_state: RuntimeEventState = RuntimeEventState.UNKNOWN,
        initial_error_kind: str = "provider_unknown",
    ) -> None:
        self._inbox = inbox
        self._session_id = session_id
        self._runtime_session_id = runtime_session_id
        self._turn_id = turn_id
        self._provider_thread_id = provider_thread_id
        self._claude_version = claude_version
        self._claim_result = claim_result
        self._on_closed = on_closed
        self._on_unusable = on_unusable
        self._initial_error = initial_error
        self._initial_error_state = initial_error_state
        self._initial_error_kind = initial_error_kind
        self._initial_emitted = False
        self._terminal_emitted = False
        self._closed = False
        self._closed_callback_called = False
        self._block_stream_ids: dict[int, str] = {}
        self._block_tool_names: dict[int, str] = {}
        self._terminal_future: asyncio.Future[RuntimeOutputEvent] = (
            asyncio.get_running_loop().create_future()
        )

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeOutputEvent:
        if self._closed or self._terminal_emitted:
            raise StopAsyncIteration
        if not self._initial_emitted:
            self._initial_emitted = True
            if self._initial_error is not None:
                await self._on_unusable(self._initial_error)
                return await self._terminal_event(
                    self._initial_error_state,
                    event_name="claudecode.turn.start.unknown",
                    error_kind=self._initial_error_kind,
                    error_message=_safe_error_message(self._initial_error),
                )
            return self._runtime_event(
                event_name="claudecode.turn.started",
                state=RuntimeEventState.STARTED,
                metadata={"provider_thread_id": self._provider_thread_id},
            )
        while not self._closed:
            try:
                message = await self._inbox.receive()
                item = await self._map_message(message)
            except asyncio.CancelledError:
                raise
            except (ClaudeProtocolError, TypeError, ValueError) as error:
                await self._on_unusable(error)
                return await self._terminal_event(
                    RuntimeEventState.UNKNOWN,
                    event_name="claudecode.turn.protocol.unknown",
                    error_kind="provider_unknown",
                    error_message=_safe_error_message(error),
                )
            except ClaudeTransportError as error:
                await self._on_unusable(error)
                return await self._terminal_event(
                    RuntimeEventState.UNKNOWN,
                    event_name="claudecode.turn.transport.unknown",
                    error_kind="provider_unknown",
                    error_message=_safe_error_message(error),
                )
            if item is not None:
                return item
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self._closed = True
        if not self._terminal_emitted:
            await self._on_unusable(
                RuntimeError("Claude turn stream closed before a terminal result")
            )
        await self._call_closed()

    async def wait_terminal(self, *, timeout: float) -> RuntimeOutputEvent:
        async with asyncio.timeout(timeout):
            return await asyncio.shield(self._terminal_future)

    async def _map_message(self, message: JsonObject) -> RuntimeOutputEvent | None:
        kind = message["type"]
        session_id = message.get("session_id")
        if kind in {
            "user",
            "assistant",
            "system",
            "result",
            "stream_event",
            "conversation_reset",
        } and not isinstance(session_id, str):
            raise ClaudeProtocolError(f"{kind} envelope requires a session_id")
        if session_id is not None and session_id != self._provider_thread_id:
            raise ClaudeProtocolError("Claude envelope belongs to another session")
        if kind == "result":
            return await self._map_result(message)
        if kind == "stream_event":
            return self._map_stream_event(message)
        if kind == "assistant":
            return self._map_assistant(message)
        if kind == "user":
            raw_message = message.get("message")
            tool_result = message.get("tool_use_result")
            if isinstance(raw_message, Mapping):
                content = raw_message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, Mapping) and block.get("type") == (
                            "tool_result"
                        ):
                            return self._text_delta(
                                _text(block.get("tool_use_id")),
                                ToolCallDeltaKind.PROGRESS,
                                _content_text(block.get("content")),
                            )
            if tool_result is not None:
                return self._text_delta(
                    _stream_id(message),
                    ToolCallDeltaKind.PROGRESS,
                    _content_text(tool_result),
                )
            return None
        if kind in {"system", "rate_limit_event"}:
            if kind == "system" and message.get("subtype") == "init":
                self._validate_cli_version(message)
            if kind == "system" and message.get("subtype") in {
                "tool_progress",
                "task_started",
                "task_notification",
                "task_updated",
            }:
                return self._text_delta(
                    _text(message.get("task_id")) or _stream_id(message),
                    ToolCallDeltaKind.PROGRESS,
                    _text(message.get("summary")),
                )
            return None
        if kind == "conversation_reset":
            await self._on_unusable(
                ClaudeProtocolError("Claude conversation was reset")
            )
            return await self._terminal_event(
                RuntimeEventState.UNKNOWN,
                event_name="claudecode.turn.conversation_reset",
                error_kind="provider_unknown",
                error_message="Claude conversation was reset",
            )
        _LOGGER.debug(
            "skipping unknown Claude envelope type", extra={"provider_type": kind}
        )
        return None

    async def _map_result(self, message: JsonObject) -> RuntimeOutputEvent | None:
        if not isinstance(message.get("subtype"), str) or not isinstance(
            message.get("is_error"), bool
        ):
            raise ClaudeProtocolError("Claude result fields are invalid")
        for field_name in ("duration_ms", "duration_api_ms", "num_turns"):
            field_value = message.get(field_name)
            if not isinstance(field_value, int) or isinstance(field_value, bool):
                raise ClaudeProtocolError(f"Claude result {field_name} is invalid")
        origin = message.get("origin")
        # A foreground turn opened during a provider-owned wake adopts that cycle,
        # so its non-human result is also the foreground terminal.
        if (
            isinstance(origin, Mapping)
            and origin.get("kind") != "human"
            and not self._inbox.adopted_provider_wake
        ):
            return None
        terminal = await self._claim_result(message)
        if not terminal:
            return None
        metadata = _result_metadata(message)
        if message.get("deferred_tool_use"):
            return await self._terminal_event(
                RuntimeEventState.FAILED,
                event_name="claudecode.turn.failed",
                error_kind="provider_deferred",
                error_message="Claude deferred a tool without executing it",
                metadata=metadata,
            )
        if (
            message.get("subtype") != "success"
            or message.get("is_error") is True
            or message.get("errors")
            or message.get("api_error_status")
        ):
            return await self._terminal_event(
                RuntimeEventState.FAILED,
                event_name="claudecode.turn.failed",
                error_kind="provider_failed",
                error_message=_result_error(message),
                metadata=metadata,
            )
        return await self._terminal_event(
            RuntimeEventState.COMPLETED,
            event_name="claudecode.turn.completed",
            metadata=metadata,
        )

    def _map_stream_event(self, message: JsonObject) -> RuntimeOutputEvent | None:
        raw_event = message.get("event")
        if not isinstance(raw_event, Mapping):
            raise ClaudeProtocolError("stream event requires an event object")
        event_type = raw_event.get("type")
        if event_type == "content_block_delta":
            delta = raw_event.get("delta")
            if not isinstance(delta, Mapping):
                raise ClaudeProtocolError("content block delta requires delta")
            delta_type = delta.get("type")
            content = delta.get("text")
            index = raw_event.get("index")
            if delta_type == "input_json_delta":
                content = delta.get("partial_json")
                stream_id = (
                    self._block_stream_ids.get(index)
                    if isinstance(index, int)
                    else None
                ) or _stream_id(message)
                return self._text_delta(
                    stream_id,
                    ToolCallDeltaKind.INPUT,
                    content if isinstance(content, str) else None,
                )
            elif delta_type == "thinking_delta":
                content_kind = ContentDeltaKind.REASONING_TEXT
            elif delta_type == "summary_text_delta":
                content_kind = ContentDeltaKind.REASONING_SUMMARY
            elif delta_type == "signature_delta":
                return None
            elif delta_type == "text_delta":
                content_kind = ContentDeltaKind.AGENT_MESSAGE
            else:
                return None
            if not isinstance(content, str):
                return None
            return self._output_event(
                ContentDelta(
                    kind=content_kind,
                    text=content,
                    index=index if isinstance(index, int) else None,
                )
            )
        if event_type == "content_block_start":
            block = raw_event.get("content_block")
            if not isinstance(block, Mapping) or block.get("type") != "tool_use":
                return None
            name = block.get("name")
            index = raw_event.get("index")
            block_id = _text(block.get("id"))
            if isinstance(index, int) and block_id is not None:
                self._block_stream_ids[index] = block_id
            if isinstance(index, int) and isinstance(name, str):
                self._block_tool_names[index] = name
            return None
        if event_type == "content_block_stop":
            index = raw_event.get("index")
            if isinstance(index, int):
                self._block_stream_ids.pop(index, None)
                self._block_tool_names.pop(index, None)
            return None
        if event_type == "message_delta":
            return None
        return None

    def _map_assistant(self, message: JsonObject) -> RuntimeOutputEvent | None:
        raw_message = message.get("message")
        if not isinstance(raw_message, Mapping):
            raise ClaudeProtocolError("assistant envelope requires a message")
        content = raw_message.get("content")
        if not isinstance(content, list):
            raise ClaudeProtocolError("assistant message content must be a list")
        for block in content:
            if not isinstance(block, Mapping):
                continue
            block_type = block.get("type")
            if block_type == "tool_result":
                return self._text_delta(
                    _text(block.get("tool_use_id")),
                    ToolCallDeltaKind.PROGRESS,
                    _content_text(block.get("content")),
                )
        return None

    def _validate_cli_version(self, message: JsonObject) -> None:
        actual = message.get("claude_code_version")
        expected = ".".join(str(part) for part in self._claude_version)
        if actual != expected:
            raise ClaudeProtocolError(
                f"Claude initialization version mismatch: expected {expected}, got {actual}"
            )

    def _runtime_event(
        self,
        *,
        event_name: str,
        state: RuntimeEventState,
        error_kind: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> RuntimeOutputEvent:
        if state is RuntimeEventState.STARTED:
            payload = TurnStarted(event_name=event_name, metadata=metadata or {})
        elif state is RuntimeEventState.COMPLETED:
            payload = TurnCompleted(event_name=event_name, metadata=metadata or {})
        elif state is RuntimeEventState.FAILED:
            payload = TurnFailed(
                event_name=event_name,
                error_kind=error_kind or "provider_failed",
                error_message=error_message,
                metadata=metadata or {},
            )
        elif state is RuntimeEventState.CANCELLED:
            payload = TurnCancelled(event_name=event_name, metadata=metadata or {})
        else:
            payload = TurnUnknown(
                event_name=event_name,
                error_kind=error_kind,
                error_message=error_message,
                metadata=metadata or {},
            )
        return self._output_event(payload)

    def _output_event(self, payload: RuntimeEventPayload) -> RuntimeOutputEvent:
        return RuntimeOutputEvent(
            envelope=RuntimeEventEnvelope(
                session_id=self._session_id,
                runtime_session_id=self._runtime_session_id,
                turn_id=self._turn_id,
                provider_turn_id=None,
                occurred_at_ms=_now_ms(),
            ),
            payload=payload,
        )

    def _text_delta(
        self,
        call_id: str | None,
        kind: ToolCallDeltaKind,
        text: str | None,
    ) -> RuntimeOutputEvent | None:
        if call_id is None or text is None:
            return None
        return self._output_event(
            ToolCallTextDelta(call_id=call_id, kind=kind, text=text)
        )

    async def _terminal_event(
        self,
        state: RuntimeEventState,
        *,
        event_name: str,
        error_kind: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> RuntimeOutputEvent:
        self._terminal_emitted = True
        event = self._runtime_event(
            event_name=event_name,
            state=state,
            error_kind=error_kind,
            error_message=error_message,
            metadata=metadata,
        )
        if not self._terminal_future.done():
            self._terminal_future.set_result(event)
        await self._call_closed()
        return event

    async def _call_closed(self) -> None:
        if self._closed_callback_called:
            return
        self._closed_callback_called = True
        await self._on_closed()


def _stream_id(message: Mapping[str, object]) -> str | None:
    return _text(message.get("parent_tool_use_id")) or _text(message.get("uuid"))


def _result_metadata(message: Mapping[str, object]) -> dict[str, JsonValue]:
    metadata: dict[str, JsonValue] = {
        "provider_thread_id": cast(JsonValue, message["session_id"]),
        "provider_subtype": cast(JsonValue, message["subtype"]),
        "duration_ms": cast(JsonValue, message["duration_ms"]),
        "duration_api_ms": cast(JsonValue, message["duration_api_ms"]),
        "num_turns": cast(JsonValue, message["num_turns"]),
    }
    for field_name in (
        "stop_reason",
        "total_cost_usd",
        "usage",
        "modelUsage",
        "permission_denials",
        "terminal_reason",
        "origin",
    ):
        value = message.get(field_name)
        if value is not None:
            metadata[field_name] = cast(JsonValue, value)
    return metadata


def _result_error(message: Mapping[str, object]) -> str:
    errors = message.get("errors")
    if isinstance(errors, list) and errors:
        return "; ".join(str(item) for item in errors)
    result = message.get("result")
    if isinstance(result, str) and result:
        return result
    return "Claude turn failed"


def _content_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if value is not None:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_error_message(error: BaseException) -> str:
    return str(error).strip() or type(error).__name__


def _now_ms() -> int:
    return time_ns() // 1_000_000


__all__ = ["TurnEventStream"]
