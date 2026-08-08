from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from time import time_ns
from typing import Self, cast
from uuid import uuid7

from ...core.approval import IApprovalHandler
from ...core.models import RuntimeEvent, RuntimeEventState
from ...core.runtime import IRuntimeTurnStream
from .approval import (
    approval_error,
    build_approval_response,
    is_approval_method,
    parse_approval_request,
)
from .client import parse_error_notification, parse_turn_notification
from .process import JsonlProcessSupervisor
from .protocol import (
    CodexAppServerProtocolError,
    JsonlMessage,
    JsonlRequestId,
    JsonlTransportError,
    is_request_id,
)


class CodexTurnEventStream(IRuntimeTurnStream):
    """Normalize one Codex turn's notifications into runtime-neutral events."""

    def __init__(
        self,
        supervisor: JsonlProcessSupervisor,
        *,
        node_id: str,
        runtime_slug: str,
        bcn_session_id: str,
        agent_runtime_session_id: str,
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
        self._node_id = node_id
        self._runtime_slug = runtime_slug
        self._bcn_session_id = bcn_session_id
        self._agent_runtime_session_id = agent_runtime_session_id
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

    async def __anext__(self) -> RuntimeEvent:
        if self._closed or self._terminal_emitted:
            raise StopAsyncIteration
        if not self._initial_emitted:
            self._initial_emitted = True
            if self._initial_error is not None:
                return self._terminal_event(
                    self._initial_error_state,
                    event_name="codex.turn.start.unknown",
                    error_kind=self._initial_error_kind,
                    error_message=_safe_error_message(self._initial_error),
                    metadata={"provider_method": "turn/start"},
                )
            return self._event(
                event_name="codex.turn.started",
                state=RuntimeEventState.STARTED,
                metadata={
                    "provider_method": "turn/start",
                    "provider_thread_id": self._provider_thread_id,
                    "provider_turn_id": self._provider_turn_id,
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
                    error_message=_safe_error_message(error),
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
                    error_message=_safe_error_message(error),
                    metadata={"provider_method": "transport"},
                )
            except (CodexAppServerProtocolError, TypeError, ValueError) as error:
                return self._terminal_event(
                    RuntimeEventState.UNKNOWN,
                    event_name="codex.turn.protocol.unknown",
                    error_kind="provider_unknown",
                    error_message=_safe_error_message(error),
                    metadata={"provider_method": "protocol"},
                )
            if event is not None:
                return event
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self._closed = True
        self._call_closed_callback()

    def _map_message(self, message: JsonlMessage) -> RuntimeEvent | None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not method:
            return None
        if not isinstance(params, Mapping):
            return None
        if not _belongs_to_thread(params, self._provider_thread_id):
            return None

        if method == "turn/completed":
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
                    error_message=turn.error_message or "Codex turn failed",
                    metadata=metadata,
                )
            if turn.status == "interrupted":
                return self._terminal_event(
                    RuntimeEventState.CANCELLED,
                    event_name="codex.turn.interrupted",
                    error_kind="cancelled",
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

        if method == "error":
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
                error_type=error.error_type,
                error_message=error.message,
                metadata=metadata,
            )

        provider_turn_id = _provider_turn_id(params)
        if (
            provider_turn_id is not None
            and self._provider_turn_id is not None
            and provider_turn_id != self._provider_turn_id
        ):
            return None
        if method in {"turn/started", "turn/progress"} or method.startswith("item/"):
            metadata = self._provider_metadata(method, params)
            if provider_turn_id is not None:
                metadata["provider_turn_id"] = provider_turn_id
            return self._event(
                event_name="codex.turn.progress",
                state=RuntimeEventState.STARTED,
                metadata=metadata,
            )
        return None

    def _event(
        self,
        *,
        event_name: str,
        state: RuntimeEventState,
        error_kind: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_seq=0,
            event_id=str(uuid7()),
            created_at_ms=time_ns() // 1_000_000,
            level="error" if error_kind else "info",
            event_name=event_name,
            state=state,
            node_id=self._node_id,
            bcn_session_id=self._bcn_session_id,
            agent_runtime_session_id=self._agent_runtime_session_id,
            turn_id=self._turn_id,
            error_kind=error_kind,
            error_type=error_type,
            error_message=error_message,
            runtime_slug=self._runtime_slug,
            metadata=dict(metadata or {}),
        )

    def _terminal_event(
        self,
        state: RuntimeEventState,
        *,
        event_name: str,
        error_kind: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> RuntimeEvent:
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
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "provider_method": method,
            "provider_thread_id": self._provider_thread_id,
        }
        if self._provider_turn_id is not None:
            metadata["provider_turn_id"] = self._provider_turn_id
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
            raise CodexAppServerProtocolError(
                "provider request id must be an integer or string"
            )
        request_id = cast(JsonlRequestId, request_id)
        if request_id in self._responded_request_ids:
            return True
        self._responded_request_ids.add(request_id)
        if not is_approval_method(method):
            await self._respond_with_error(
                request_id,
                CodexAppServerProtocolError(
                    f"unsupported provider request method: {method}"
                ),
            )
            raise CodexAppServerProtocolError("unsupported provider request")
        response_attempted = False
        try:
            approval = parse_approval_request(
                message,
                bcn_session_id=self._bcn_session_id,
                agent_runtime_session_id=self._agent_runtime_session_id,
                turn_id=self._turn_id,
                provider_thread_id=self._provider_thread_id,
                provider_turn_id=self._provider_turn_id,
            )
            if self._approval_handler is None:
                raise CodexAppServerProtocolError(
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
            raise CodexAppServerProtocolError(
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


def _belongs_to_thread(params: Mapping[str, object], thread_id: str) -> bool:
    value = params.get("threadId")
    return value == thread_id


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


def _safe_error_message(error: BaseException) -> str:
    message = str(error).strip()
    return message or type(error).__name__


__all__ = ["CodexTurnEventStream"]
