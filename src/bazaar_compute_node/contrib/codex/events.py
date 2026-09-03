from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from time import time_ns
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ...core.approval import IApprovalHandler
from ...core.models import (
    ContentDelta,
    ContentDeltaKind,
    ContextCompactionCompleted,
    ContextCompactionStarted,
    FileChangeEntry,
    JsonValue,
    RuntimeEventEnvelope,
    RuntimeEventPayload,
    RuntimeEventState,
    RuntimeOutputEvent,
    TokenUsage,
    ToolCall,
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
from ...core.runtime import IRuntimeTurnStream
from ...core.utils.text import format_exception
from .approval import (
    approval_error,
    build_approval_response,
    is_approval_method,
    parse_approval_request,
)
from .client import parse_error_notification, parse_turn_notification
from .process import JsonlProcessSupervisor
from .protocol import (
    AppServerProtocolError,
    JsonlMessage,
    JsonlRequestId,
    JsonlTransportError,
    is_request_id,
)

_CONTENT_DELTA_KINDS = {
    "item/agentMessage/delta": ContentDeltaKind.AGENT_MESSAGE,
    "item/plan/delta": ContentDeltaKind.PLAN,
    "item/reasoning/summaryTextDelta": ContentDeltaKind.REASONING_SUMMARY,
    "item/reasoning/textDelta": ContentDeltaKind.REASONING_TEXT,
}
_IGNORED_METHODS = frozenset(
    {
        "item/reasoning/summaryPartAdded",
        "item/autoApprovalReview/started",
        "item/autoApprovalReview/completed",
    }
)
_CONTENT_ITEM_TYPES = frozenset(
    {"userMessage", "hookPrompt", "agentMessage", "plan", "reasoning"}
)
_THREAD_STATE_ITEM_TYPES = frozenset({"enteredReviewMode", "exitedReviewMode"})
_TOOL_TYPE_NAMES = {
    "commandExecution": "command",
    "fileChange": "file_change",
    "mcpToolCall": "mcp_tool",
    "dynamicToolCall": "dynamic_tool",
    "collabAgentToolCall": "agent",
    "subAgentActivity": "agent",
    "webSearch": "web_search",
    "imageView": "image_view",
    "imageGeneration": "image_generation",
    "sleep": "sleep",
    "functionCallOutput": "function",
}
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


class _AddPatchChange(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    type: Literal["add"]


class _DeletePatchChange(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    type: Literal["delete"]


class _UpdatePatchChange(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    type: Literal["update"]
    move_path: str | None = None


type _PatchChangeKind = Annotated[
    _AddPatchChange | _DeletePatchChange | _UpdatePatchChange,
    Field(discriminator="type"),
]


class _FileUpdateChange(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    path: str = Field(min_length=1)
    kind: _PatchChangeKind
    diff: str


class _FileChangePatchUpdatedNotification(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    thread_id: str = Field(alias="threadId", min_length=1)
    turn_id: str = Field(alias="turnId", min_length=1)
    item_id: str = Field(alias="itemId", min_length=1)
    changes: list[_FileUpdateChange]


_FILE_CHANGE_PATCH_UPDATED_ADAPTER = TypeAdapter(_FileChangePatchUpdatedNotification)


class TurnEventStream(IRuntimeTurnStream):
    """Normalize one Codex turn's notifications into runtime-neutral items."""

    def __init__(
        self,
        supervisor: JsonlProcessSupervisor,
        *,
        session_id: str,
        runtime_session_id: str,
        turn_id: str,
        provider_thread_id: str,
        provider_turn_id: str | None,
        approval_handler: IApprovalHandler | None = None,
        approval_timeout: float = 30,
        initial_error: BaseException | None = None,
        initial_error_kind: str = "provider_unknown",
        initial_error_state: RuntimeEventState = RuntimeEventState.UNKNOWN,
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._session_id = session_id
        self._runtime_session_id = runtime_session_id
        self._turn_id = turn_id
        self._provider_thread_id = provider_thread_id
        self._provider_turn_id = provider_turn_id
        self._approval_handler = approval_handler
        self._approval_timeout = approval_timeout
        self._responded_request_ids: set[JsonlRequestId] = set()
        self._initial_error = initial_error
        self._initial_error_kind = initial_error_kind
        self._initial_error_state = initial_error_state
        self._initial_emitted = False
        self._terminal_emitted = False
        self._closed = False
        self._on_closed = on_closed
        self._closed_callback_called = False

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeOutputEvent:
        if self._closed or self._terminal_emitted:
            raise StopAsyncIteration
        if not self._initial_emitted:
            self._initial_emitted = True
            if self._initial_error is not None:
                return self._terminal_event(
                    self._initial_error_state,
                    event_name="codex.turn.start.unknown",
                    error_kind=self._initial_error_kind,
                    error_message=format_exception(self._initial_error),
                    metadata={"provider_method": "turn/start"},
                )
            return self._event(
                event_name="codex.turn.started",
                state=RuntimeEventState.STARTED,
                metadata={
                    "provider_method": "turn/start",
                    "provider_thread_id": self._provider_thread_id,
                },
            )

        while not self._closed:
            try:
                message = await self._supervisor.receive()
            except JsonlTransportError as error:
                return self._terminal_event(
                    RuntimeEventState.UNKNOWN,
                    event_name="codex.turn.transport.unknown",
                    error_kind="provider_unknown",
                    error_message=format_exception(error),
                    metadata={"provider_method": "transport"},
                )
            try:
                if await self._handle_provider_request(message):
                    continue
                event = self._map_message(message)
            except JsonlTransportError as error:
                return self._terminal_event(
                    RuntimeEventState.UNKNOWN,
                    event_name="codex.turn.transport.unknown",
                    error_kind="provider_unknown",
                    error_message=format_exception(error),
                    metadata={"provider_method": "transport"},
                )
            except (AppServerProtocolError, TypeError, ValueError) as error:
                return self._terminal_event(
                    RuntimeEventState.UNKNOWN,
                    event_name="codex.turn.protocol.unknown",
                    error_kind="provider_unknown",
                    error_message=format_exception(error),
                    metadata={"provider_method": "protocol"},
                )
            if event is not None:
                return event
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self._closed = True
        self._call_closed_callback()

    def _map_turn_completed(
        self, message: JsonlMessage, method: str, params: Mapping[str, object]
    ) -> RuntimeOutputEvent | None:
        """Read a finished turn's status as this stream's terminal event."""

        thread_id, turn = parse_turn_notification(message)
        if thread_id != self._provider_thread_id or turn.turn_id != (
            self._provider_turn_id
        ):
            return None
        metadata = self._provider_metadata(method, params)
        metadata["provider_status"] = turn.status
        if turn.status == "completed":
            return self._terminal_event(
                RuntimeEventState.COMPLETED,
                event_name="codex.turn.completed",
                metadata=metadata,
            )
        if turn.status == "failed":
            return self._terminal_event(
                RuntimeEventState.FAILED,
                event_name="codex.turn.failed",
                error_kind="provider_failed",
                error_message=turn.error_message,
                metadata=metadata,
            )
        return self._terminal_event(
            RuntimeEventState.UNKNOWN,
            event_name="codex.turn.unknown",
            error_kind="provider_unknown",
            error_message=f"Unsupported Codex turn status: {turn.status}",
            metadata=metadata,
        )

    def _map_error(
        self, message: JsonlMessage, method: str, params: Mapping[str, object]
    ) -> RuntimeOutputEvent | None:
        """Say a provider error happened, without ending the turn it happened in."""

        error = parse_error_notification(message)
        if error.thread_id != self._provider_thread_id or (
            self._provider_turn_id is not None
            and error.turn_id != self._provider_turn_id
        ):
            return None
        metadata = self._provider_metadata(method, params)
        metadata["will_retry"] = error.will_retry
        if error.error_type is not None:
            metadata["provider_error_type"] = error.error_type
        return self._event(
            event_name="codex.turn.error",
            state=RuntimeEventState.STARTED,
            metadata=metadata,
        )

    def _map_token_usage(
        self, params: Mapping[str, object]
    ) -> RuntimeOutputEvent | None:
        """Report what the thread has spent so far."""

        raw_usage = params.get("tokenUsage")
        if not isinstance(raw_usage, Mapping):
            raise AppServerProtocolError("token usage notification requires tokenUsage")
        raw_total = raw_usage.get("total")
        raw_last = raw_usage.get("last")
        if not isinstance(raw_total, Mapping) or not isinstance(raw_last, Mapping):
            raise AppServerProtocolError("token usage requires total and last")
        model_context_window = raw_usage.get("modelContextWindow")
        if model_context_window is not None and (
            not isinstance(model_context_window, int)
            or isinstance(model_context_window, bool)
        ):
            raise AppServerProtocolError("model context window must be an integer")
        return self._output_event(
            UsageUpdated(
                total=_token_usage(raw_total),
                last=_token_usage(raw_last),
                model_context_window=model_context_window,
            )
        )

    def _map_content_delta(
        self, method: str, params: Mapping[str, object]
    ) -> RuntimeOutputEvent | None:
        """Pass along a chunk of the model's own text."""

        content = params.get("delta")
        if not isinstance(content, str):
            return None
        index = None
        if method == "item/reasoning/summaryTextDelta":
            index = params.get("summaryIndex")
        elif method == "item/reasoning/textDelta":
            index = params.get("contentIndex")
        return self._output_event(
            ContentDelta(
                kind=_CONTENT_DELTA_KINDS[method],
                text=content,
                index=(
                    index
                    if isinstance(index, int) and not isinstance(index, bool)
                    else None
                ),
            )
        )

    def _map_patch_updated(
        self, params: Mapping[str, object]
    ) -> RuntimeOutputEvent | None:
        """Carry a file change's diff as it is revised."""

        try:
            notification = _FILE_CHANGE_PATCH_UPDATED_ADAPTER.validate_python(
                params, strict=True
            )
        except ValidationError as error:
            raise AppServerProtocolError(
                "file change patch update notification is invalid"
            ) from error
        return self._output_event(
            ToolCallPatchUpdated(
                call_id=notification.item_id,
                changes=tuple(
                    FileChangeEntry(
                        path=change.path,
                        kind=change.kind.type,
                        patch=change.diff,
                    )
                    for change in notification.changes
                ),
            )
        )

    def _map_terminal_interaction(
        self, params: Mapping[str, object], stream_id: str
    ) -> RuntimeOutputEvent | None:
        """Record what was typed into a running command."""

        stdin = params.get("stdin")
        if not isinstance(stdin, str):
            return None
        process_id = params.get("processId")
        return self._output_event(
            ToolCallInteraction(
                call_id=stream_id,
                stdin=stdin,
                process_id=process_id if isinstance(process_id, str) else None,
            )
        )

    def _map_tool_text_delta(
        self, method: str, params: Mapping[str, object], stream_id: str
    ) -> RuntimeOutputEvent | None:
        """Pass along a tool's own output or progress chatter."""

        notification = method.rsplit("/", maxsplit=1)[-1]
        if not method.startswith("item/") or notification not in {
            "outputDelta",
            "progress",
        }:
            return None
        output = notification == "outputDelta"
        content = params.get("delta") if output else params.get("message")
        if not isinstance(content, str):
            return None
        return self._output_event(
            ToolCallTextDelta(
                call_id=stream_id,
                kind=ToolCallDeltaKind.OUTPUT if output else ToolCallDeltaKind.PROGRESS,
                text=content,
            )
        )

    def _map_item_lifecycle(
        self,
        method: str,
        params: Mapping[str, object],
        provider_turn_id: str | None,
    ) -> RuntimeOutputEvent | None:
        """Turn an item's start or finish into the tool call it stands for."""

        item = params.get("item")
        if not isinstance(item, Mapping):
            raise AppServerProtocolError("item lifecycle requires an item object")
        item_type = item.get("type")
        if not isinstance(item_type, str) or not item_type:
            raise AppServerProtocolError("item lifecycle requires an item type")
        if item_type in _CONTENT_ITEM_TYPES or item_type in _THREAD_STATE_ITEM_TYPES:
            return None
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            return None
        timestamp_field = "startedAtMs" if method == "item/started" else "completedAtMs"
        occurred_at_ms = params.get(timestamp_field)
        if not isinstance(occurred_at_ms, int) or isinstance(occurred_at_ms, bool):
            raise AppServerProtocolError(f"item lifecycle requires {timestamp_field}")
        if item_type == "contextCompaction":
            payload: RuntimeEventPayload = (
                ContextCompactionStarted(compaction_id=call_id)
                if method == "item/started"
                else ContextCompactionCompleted(compaction_id=call_id)
            )
            return self._output_event(
                payload,
                occurred_at_ms=occurred_at_ms,
                provider_turn_id=provider_turn_id,
            )
        value = _tool_call_value(method, item)
        started = method == "item/started"
        call = ToolCall(
            call_id=call_id,
            name=_tool_call_name(item, item_type),
            input=value if started else None,
            output=None if started else value,
        )
        status = item.get("status")
        error = item.get("error")
        failed = (
            isinstance(status, str) and status in {"failed", "declined", "errored"}
        ) or error is not None
        if started:
            payload = ToolCallStarted(call=call)
        elif failed:
            if isinstance(error, Mapping):
                error = error.get("message")
            payload = ToolCallFailed(
                call=call, error_message=error if isinstance(error, str) else None
            )
        else:
            payload = ToolCallCompleted(call=call)
        return self._output_event(
            payload,
            occurred_at_ms=occurred_at_ms,
            provider_turn_id=provider_turn_id,
        )

    def _map_message(self, message: JsonlMessage) -> RuntimeOutputEvent | None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not method:
            return None
        if not isinstance(params, Mapping):
            return None
        if params.get("threadId") != self._provider_thread_id:
            return None

        if method == "turn/completed":
            return self._map_turn_completed(message, method, params)
        if method == "error":
            return self._map_error(message, method, params)
        provider_turn_id = _provider_turn_id(params)
        if (
            provider_turn_id is not None
            and self._provider_turn_id is not None
            and provider_turn_id != self._provider_turn_id
        ):
            return None
        if method in _IGNORED_METHODS:
            return None
        if method in {"item/started", "item/completed"}:
            return self._map_item_lifecycle(method, params, provider_turn_id)
        if method == "thread/tokenUsage/updated":
            return self._map_token_usage(params)
        if method in _CONTENT_DELTA_KINDS:
            return self._map_content_delta(method, params)
        if method == "item/fileChange/patchUpdated":
            return self._map_patch_updated(params)
        stream_id = params.get("itemId")
        if not isinstance(stream_id, str) or not stream_id:
            return None
        if method == "item/commandExecution/terminalInteraction":
            return self._map_terminal_interaction(params, stream_id)
        return self._map_tool_text_delta(method, params, stream_id)

    def _event(
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

    def _output_event(
        self,
        payload: RuntimeEventPayload,
        *,
        occurred_at_ms: int | None = None,
        provider_turn_id: str | None = None,
    ) -> RuntimeOutputEvent:
        return RuntimeOutputEvent(
            envelope=RuntimeEventEnvelope(
                session_id=self._session_id,
                runtime_session_id=self._runtime_session_id,
                turn_id=self._turn_id,
                provider_turn_id=provider_turn_id or self._provider_turn_id,
                occurred_at_ms=(
                    occurred_at_ms
                    if occurred_at_ms is not None
                    else time_ns() // 1_000_000
                ),
            ),
            payload=payload,
        )

    def _terminal_event(
        self,
        state: RuntimeEventState,
        *,
        event_name: str,
        error_kind: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> RuntimeOutputEvent:
        self._terminal_emitted = True
        self._call_closed_callback()
        return self._event(
            event_name=event_name,
            state=state,
            error_kind=error_kind,
            error_message=error_message,
            metadata=metadata,
        )

    def _provider_metadata(
        self,
        method: str,
        params: Mapping[str, object],
    ) -> dict[str, JsonValue]:
        metadata: dict[str, JsonValue] = {
            "provider_method": method,
            "provider_thread_id": self._provider_thread_id,
        }
        request_id = params.get("requestId")
        if isinstance(request_id, (int, str)) and not isinstance(request_id, bool):
            metadata["provider_request_id"] = str(request_id)
        return metadata

    def _call_closed_callback(self) -> None:
        if self._closed_callback_called:
            return
        self._closed_callback_called = True
        if self._on_closed is not None:
            self._on_closed()

    async def _handle_provider_request(self, message: JsonlMessage) -> bool:
        method = message.get("method")
        request_id = message.get("id")
        if not isinstance(method, str) or request_id is None:
            return False
        if not is_request_id(request_id):
            raise AppServerProtocolError(
                "provider request id must be an integer or string"
            )
        request_id = cast(JsonlRequestId, request_id)
        if request_id in self._responded_request_ids:
            return True
        self._responded_request_ids.add(request_id)
        if not is_approval_method(method):
            await self._respond_with_error(
                request_id,
                AppServerProtocolError(
                    f"unsupported provider request method: {method}"
                ),
            )
            raise AppServerProtocolError("unsupported provider request")
        response_attempted = False
        try:
            approval = parse_approval_request(
                message,
                session_id=self._session_id,
                runtime_session_id=self._runtime_session_id,
                turn_id=self._turn_id,
                provider_thread_id=self._provider_thread_id,
                provider_turn_id=self._provider_turn_id,
            )
            if self._approval_handler is None:
                raise AppServerProtocolError(
                    "runtime approval handler is not configured"
                )
            result = await self._approval_handler.request_approval(
                approval.request,
                timeout=self._approval_timeout,
            )
            response = build_approval_response(approval, result)
            response_attempted = True
            await self._supervisor.respond(
                request_id,
                result=response,
                timeout=self._approval_timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not response_attempted:
                await self._respond_with_error(request_id, error)
            if response_attempted and isinstance(error, JsonlTransportError):
                raise
            raise AppServerProtocolError(
                f"approval bridge failed: {type(error).__name__}"
            ) from error
        return True

    async def _respond_with_error(
        self,
        request_id: JsonlRequestId,
        error: BaseException,
    ) -> None:
        await self._supervisor.respond(
            request_id,
            error=approval_error(error),
            timeout=self._approval_timeout,
        )


def _tool_call_name(item: Mapping[str, object], item_type: str) -> str:
    """Name a tool call the way its provider spelled it, falling back by type."""

    server = item.get("server")
    tool = item.get("tool")
    if isinstance(server, str) and server and isinstance(tool, str) and tool:
        return f"{server}/{tool}"
    if isinstance(tool, str) and tool:
        return tool
    value = item.get("name")
    if isinstance(value, str) and value:
        return value
    command = item.get("command")
    if isinstance(command, str):
        parts = command.strip().split(maxsplit=1)
        if parts:
            return parts[0]
    return _TOOL_TYPE_NAMES.get(item_type, "tool")


def _tool_call_value(method: str, item: Mapping[str, object]) -> JsonValue:
    """Read whichever field carries a call's arguments or its result."""

    fields = (
        ("arguments", "input", "command")
        if method == "item/started"
        else ("result", "output", "contentItems")
    )
    for field in fields:
        if field not in item:
            continue
        try:
            return _JSON_VALUE_ADAPTER.validate_python(item[field], strict=True)
        except ValueError as error:
            raise AppServerProtocolError(
                f"item lifecycle {field} must be a JSON value"
            ) from error
    return None


def _provider_turn_id(params: Mapping[str, object]) -> str | None:
    value = params.get("turnId")
    if isinstance(value, str) and value:
        return value
    turn = params.get("turn")
    if isinstance(turn, Mapping):
        value = turn.get("id")
        if isinstance(value, str) and value:
            return value
    return None


def _token_usage(value: Mapping[str, object]) -> TokenUsage:
    fields = {
        "input_tokens": value.get("inputTokens"),
        "cached_input_tokens": value.get("cachedInputTokens"),
        "cache_write_input_tokens": value.get("cacheWriteInputTokens"),
        "output_tokens": value.get("outputTokens"),
        "reasoning_output_tokens": value.get("reasoningOutputTokens"),
        "total_tokens": value.get("totalTokens"),
    }
    if any(
        token_count is not None
        and (not isinstance(token_count, int) or isinstance(token_count, bool))
        for token_count in fields.values()
    ):
        raise AppServerProtocolError("token usage values must be integers")
    checked = cast(dict[str, int | None], fields)
    reported_input = checked["input_tokens"]
    cached_input = checked["cached_input_tokens"]
    if reported_input is not None and cached_input is not None:
        checked["input_tokens"] = max(0, reported_input - cached_input)
    return TokenUsage(**checked)


__all__ = ["TurnEventStream"]
