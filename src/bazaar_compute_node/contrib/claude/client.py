from __future__ import annotations

import asyncio
import secrets
from collections.abc import Mapping

from .process import ProcessSupervisor
from .protocol import (
    ClaudeProcessExited,
    ClaudeProtocolError,
    JsonObject,
    parse_control_response,
)


class Client:
    """Correlate Claude CLI control traffic while retaining business envelopes."""

    def __init__(self, supervisor: ProcessSupervisor) -> None:
        self._supervisor = supervisor
        self._messages: asyncio.Queue[JsonObject] = asyncio.Queue(maxsize=100)
        self._pending: dict[str, asyncio.Future[JsonObject]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._request_counter = 0

    @property
    def pending_control_count(self) -> int:
        return len(self._pending)

    def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(
                self._read_messages(), name="claude-code-client"
            )

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
        return await self._messages.get()

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
        task = self._reader_task
        self._reader_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._fail_pending(ClaudeProcessExited(None, ()))

    async def _read_messages(self) -> None:
        try:
            while True:
                envelope = await self._supervisor.receive()
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
                await self._messages.put(envelope)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._fail_pending(error)

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
