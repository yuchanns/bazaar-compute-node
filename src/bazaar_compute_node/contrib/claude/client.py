from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping

from .process import ProcessSupervisor
from .protocol import (
    ClaudeProcessExited,
    ClaudeProtocolError,
    JsonObject,
    parse_control_response,
)

_LOGGER = logging.getLogger(__name__)


class Client:
    """Correlate Claude CLI control traffic while retaining business envelopes."""

    def __init__(self, supervisor: ProcessSupervisor) -> None:
        self._supervisor = supervisor
        self._messages: asyncio.Queue[JsonObject | BaseException] = asyncio.Queue(
            maxsize=100
        )
        self._pending: dict[str, asyncio.Future[JsonObject]] = {}
        self._inflight_requests: dict[str, asyncio.Task[None]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._message_observer: Callable[[JsonObject], None] | None = None
        self._control_request_handler: (
            Callable[[JsonObject], Awaitable[Mapping[str, object]]] | None
        ) = None
        self._request_counter = 0

    @property
    def pending_control_count(self) -> int:
        return len(self._pending)

    def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(
                self._read_messages(), name="claude-code-client"
            )

    def set_message_observer(
        self, observer: Callable[[JsonObject], None] | None
    ) -> None:
        self._message_observer = observer

    def set_control_request_handler(
        self,
        handler: Callable[[JsonObject], Awaitable[Mapping[str, object]]] | None,
    ) -> None:
        self._control_request_handler = handler

    async def clear_control_request_handler(
        self,
        handler: Callable[[JsonObject], Awaitable[Mapping[str, object]]],
    ) -> None:
        if self._control_request_handler is not handler:
            return
        self._control_request_handler = None
        tasks = tuple(self._inflight_requests.values())
        self._inflight_requests.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def initialize(self, *, timeout: float) -> JsonObject:
        return await self.control(
            {"subtype": "initialize", "hooks": None}, timeout=timeout
        )

    async def control(
        self, request: Mapping[str, object], *, timeout: float
    ) -> JsonObject:
        subtype = request.get("subtype")
        if not isinstance(subtype, str) or not subtype:
            raise ClaudeProtocolError("control request requires a subtype")
        self.start()
        request_id = self._next_request_id()
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(timeout):
                await self._supervisor.send(
                    {
                        "type": "control_request",
                        "request_id": request_id,
                        "request": dict(request),
                    },
                    timeout=timeout,
                )
                response = await asyncio.shield(future)
            return parse_control_response(response)
        finally:
            if self._pending.get(request_id) is future:
                self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def receive(self) -> JsonObject:
        item = await self._messages.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def send_user_message(self, text: str, *, timeout: float) -> None:
        await self._supervisor.send(
            {
                "type": "user",
                "session_id": "",
                "message": {"role": "user", "content": text},
                "parent_tool_use_id": None,
            },
            timeout=timeout,
        )

    async def close(self) -> None:
        self._control_request_handler = None
        requests = tuple(self._inflight_requests.values())
        self._inflight_requests.clear()
        for request in requests:
            request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        task = self._reader_task
        self._reader_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        error = ClaudeProcessExited(
            self._supervisor.returncode, self._supervisor.stderr_tail
        )
        self._fail_pending(error)
        self._publish_failure(error)

    async def _read_messages(self) -> None:
        try:
            while True:
                envelope = await self._supervisor.receive()
                if self._message_observer is not None:
                    self._message_observer(envelope)
                if envelope["type"] == "control_response":
                    response = envelope["response"]
                    request_id = (
                        response.get("request_id")
                        if isinstance(response, Mapping)
                        else None
                    )
                    if isinstance(request_id, str):
                        future = self._pending.pop(request_id, None)
                        if future is not None and not future.done():
                            future.set_result(envelope)
                    continue
                if envelope["type"] == "control_request":
                    request_id = envelope.get("request_id")
                    if not isinstance(request_id, str) or not request_id:
                        await self._messages.put(
                            ClaudeProtocolError(
                                "incoming control request requires a request_id"
                            )
                        )
                        continue
                    task = asyncio.create_task(
                        self._handle_control_request(envelope),
                        name=f"claude-code-control-{request_id}",
                    )
                    self._inflight_requests[request_id] = task
                    task.add_done_callback(
                        lambda completed, key=request_id: self._discard_request(
                            key, completed
                        )
                    )
                    continue
                if envelope["type"] == "control_cancel_request":
                    request_id = envelope.get("request_id")
                    if isinstance(request_id, str):
                        task = self._inflight_requests.pop(request_id, None)
                        if task is not None:
                            task.cancel()
                    continue
                await self._messages.put(envelope)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._fail_pending(error)
            self._publish_failure(error)

    async def _handle_control_request(self, envelope: JsonObject) -> None:
        request_id = envelope["request_id"]
        assert isinstance(request_id, str)
        try:
            handler = self._control_request_handler
            if handler is None:
                raise ClaudeProtocolError(
                    "Claude control request has no active turn handler"
                )
            response = await handler(envelope)
            await self._supervisor.send(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": request_id,
                        "response": dict(response),
                    },
                },
                timeout=10,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            _LOGGER.warning(
                "Claude control request handler failed",
                extra={
                    "provider_request_id": request_id,
                    "error_kind": type(error).__name__,
                },
            )
            try:
                await self._supervisor.send(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "error",
                            "request_id": request_id,
                            "error": f"permission bridge failed: {type(error).__name__}",
                        },
                    },
                    timeout=10,
                )
            except Exception as send_error:  # noqa: BLE001
                self._publish_failure(send_error)

    def _discard_request(self, request_id: str, completed: asyncio.Task[None]) -> None:
        if self._inflight_requests.get(request_id) is completed:
            self._inflight_requests.pop(request_id, None)
        if not completed.cancelled():
            completed.exception()

    def _publish_failure(self, error: BaseException) -> None:
        try:
            self._messages.put_nowait(error)
        except asyncio.QueueFull:
            self._messages.get_nowait()
            self._messages.put_nowait(error)

    def _fail_pending(self, error: BaseException) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def _next_request_id(self) -> str:
        self._request_counter += 1
        return f"req_{self._request_counter}_{secrets.token_hex(4)}"


__all__ = ["Client"]
