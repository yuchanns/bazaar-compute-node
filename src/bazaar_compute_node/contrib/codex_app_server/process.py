from __future__ import annotations

import asyncio
import json
import os
import signal
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from .protocol import (
    JsonlMessage,
    JsonlProcessExited,
    JsonlProcessNotRunning,
    JsonlProtocolError,
    JsonlRemoteError,
    JsonlRequestId,
    JsonlRequestTimeout,
    JsonlTransportError,
    is_request_id,
    validate_message,
)

StderrHandler = Callable[[str], Awaitable[None] | None]


class JsonlProcessState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    EXITED = "exited"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JsonlProcessSpec:
    executable: str
    arguments: tuple[str, ...] = ()
    cwd: Path | None = None
    environment: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.executable:
            raise ValueError("executable must be a non-empty string")
        if any(not isinstance(argument, str) for argument in self.arguments):
            raise TypeError("arguments must contain only strings")
        if self.cwd is not None and not isinstance(self.cwd, Path):
            raise TypeError("cwd must be a Path or None")
        if self.environment is not None and any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.environment.items()
        ):
            raise TypeError("environment must contain only string keys and values")

    @property
    def command(self) -> tuple[str, ...]:
        return (self.executable, *self.arguments)


_QUEUE_CLOSED = object()


class JsonlProcessSupervisor:
    """Own one subprocess speaking newline-delimited JSON over stdio."""

    def __init__(
        self,
        spec: JsonlProcessSpec,
        *,
        stderr_tail_limit: int = 64,
        stderr_handler: StderrHandler | None = None,
    ) -> None:
        if stderr_tail_limit <= 0:
            raise ValueError("stderr_tail_limit must be positive")
        self.spec = spec
        self._stderr_tail: deque[str] = deque(maxlen=stderr_tail_limit)
        self._stderr_handler = stderr_handler
        self._process: asyncio.subprocess.Process | None = None
        self._state = JsonlProcessState.STOPPED
        self._returncode: int | None = None
        self._fatal_error: JsonlTransportError | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._exit_event = asyncio.Event()
        self._incoming: asyncio.Queue[JsonlMessage | object] = asyncio.Queue()
        self._pending: dict[JsonlRequestId, asyncio.Future[JsonlMessage]] = {}
        self._next_request_id = 0
        self._closed_message_sent = False

    @property
    def state(self) -> JsonlProcessState:
        return self._state

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.returncode is None

    @property
    def fatal_error(self) -> JsonlTransportError | None:
        return self._fatal_error

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr_tail)

    async def start(self, *, timeout: float) -> None:
        _validate_timeout(timeout)
        async with self._lifecycle_lock:
            if self.is_running:
                return
            await self._join_tasks()
            self._reset_runtime_state()
            self._state = JsonlProcessState.STARTING
            try:
                async with asyncio.timeout(timeout):
                    self._process = await asyncio.create_subprocess_exec(
                        *self.spec.command,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=(str(self.spec.cwd) if self.spec.cwd is not None else None),
                        env=(
                            dict(self.spec.environment)
                            if self.spec.environment is not None
                            else None
                        ),
                    )
            except BaseException:
                self._process = None
                self._state = JsonlProcessState.FAILED
                raise
            self._state = JsonlProcessState.RUNNING
            process = self._process
            self._stdout_task = asyncio.create_task(
                self._read_stdout(process),
                name="codex-app-server-stdout",
            )
            self._stderr_task = asyncio.create_task(
                self._read_stderr(process),
                name="codex-app-server-stderr",
            )
            self._watch_task = asyncio.create_task(
                self._watch_process(process),
                name="codex-app-server-process",
            )

    async def stop(self, *, timeout: float) -> None:
        _validate_timeout(timeout)
        async with self._lifecycle_lock:
            process = self._process
            if process is None:
                await self._join_tasks()
                self._state = JsonlProcessState.STOPPED
                self._send_closed_message()
                return
            self._state = JsonlProcessState.STOPPING
            deadline = asyncio.get_running_loop().time() + timeout
            if process.stdin is not None:
                process.stdin.close()
            try:
                await self._wait_for_process(process, deadline)
            except TimeoutError:
                _terminate_process(process)
                try:
                    await self._wait_for_process(process, deadline)
                except TimeoutError:
                    _kill_process(process)
                    await process.wait()
            await self._join_tasks()
            self._state = JsonlProcessState.STOPPED
            self._send_closed_message()

    async def wait(self, *, timeout: float | None = None) -> int | None:
        if self._process is None:
            return self._returncode
        if timeout is None:
            await self._exit_event.wait()
        else:
            _validate_timeout(timeout)
            async with asyncio.timeout(timeout):
                await self._exit_event.wait()
        return self._returncode

    async def request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        timeout: float,
    ) -> JsonlMessage:
        _validate_method(method)
        _validate_timeout(timeout)
        self._ensure_running()
        request_id = self._next_id()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        payload: JsonlMessage = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        try:
            async with asyncio.timeout(timeout):
                await self._write_message(payload)
                return await asyncio.shield(future)
        except TimeoutError:
            self._remove_pending(request_id, future)
            raise JsonlRequestTimeout(request_id=request_id, method=method) from None
        except asyncio.CancelledError:
            self._remove_pending(request_id, future)
            raise
        except BaseException:
            self._remove_pending(request_id, future)
            raise

    async def notify(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        timeout: float,
    ) -> None:
        _validate_method(method)
        _validate_timeout(timeout)
        self._ensure_running()
        payload: JsonlMessage = {"method": method}
        if params is not None:
            payload["params"] = dict(params)
        async with asyncio.timeout(timeout):
            await self._write_message(payload)

    async def receive(self, *, timeout: float | None = None) -> JsonlMessage:
        if timeout is None:
            item = await self._incoming.get()
        else:
            _validate_timeout(timeout)
            async with asyncio.timeout(timeout):
                item = await self._incoming.get()
        if item is _QUEUE_CLOSED:
            error = self._fatal_error
            if error is not None:
                raise error
            raise JsonlProcessExited(
                returncode=self._returncode,
                stderr_tail=self.stderr_tail,
            )
        return cast(JsonlMessage, item)

    async def incoming(self) -> AsyncIterator[JsonlMessage]:
        while True:
            yield await self.receive()

    async def _write_message(self, payload: Mapping[str, object]) -> None:
        message = validate_message(payload)
        try:
            encoded = (
                json.dumps(
                    message,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError) as error:
            raise JsonlProtocolError(
                "outgoing JSONL message is not serializable"
            ) from error
        async with self._write_lock:
            self._ensure_running()
            process = self._process
            if process is None or process.stdin is None:
                raise JsonlProcessNotRunning()
            try:
                process.stdin.write(encoded)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionError, OSError) as error:
                raise JsonlProcessExited(
                    returncode=process.returncode,
                    stderr_tail=self.stderr_tail,
                ) from error

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        stdout = process.stdout
        if stdout is None:
            await self._protocol_failure("stdout pipe is unavailable")
            return
        line_number = 0
        try:
            while line := await stdout.readline():
                line_number += 1
                try:
                    decoded = line.decode("utf-8")
                    payload = json.loads(decoded)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    await self._protocol_failure(
                        "stdout contains invalid JSONL",
                        line_number=line_number,
                    )
                    if isinstance(error, UnicodeDecodeError):
                        return
                    return
                if not isinstance(payload, dict):
                    await self._protocol_failure(
                        "stdout JSONL item must be an object",
                        line_number=line_number,
                    )
                    return
                self._route_message(cast(JsonlMessage, payload))
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as error:
            await self._protocol_failure(f"stdout read failed: {type(error).__name__}")

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        stderr = process.stderr
        if stderr is None:
            return
        try:
            while line := await stderr.readline():
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                self._stderr_tail.append(text)
                if self._stderr_handler is not None:
                    result = self._stderr_handler(text)
                    if result is not None:
                        await result
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError):
            return

    async def _watch_process(self, process: asyncio.subprocess.Process) -> None:
        returncode = await process.wait()
        self._returncode = returncode
        stdout_task = self._stdout_task
        if stdout_task is not None and stdout_task is not asyncio.current_task():
            await asyncio.gather(stdout_task, return_exceptions=True)
        stderr_task = self._stderr_task
        if stderr_task is not None and stderr_task is not asyncio.current_task():
            await asyncio.gather(stderr_task, return_exceptions=True)
        if self._fatal_error is None and self._pending:
            await self._fail_pending(
                JsonlProcessExited(
                    returncode=returncode,
                    stderr_tail=self.stderr_tail,
                )
            )
        if self._fatal_error is None and returncode != 0:
            self._fatal_error = JsonlProcessExited(
                returncode=returncode,
                stderr_tail=self.stderr_tail,
            )
        self._state = (
            JsonlProcessState.FAILED
            if self._fatal_error is not None or returncode != 0
            else JsonlProcessState.EXITED
        )
        self._exit_event.set()
        self._send_closed_message()

    def _route_message(self, payload: JsonlMessage) -> None:
        raw_id = payload.get("id")
        if raw_id is not None and not is_request_id(raw_id):
            self._schedule_protocol_failure("JSONL message id is invalid")
            return
        if raw_id is not None and "method" not in payload:
            future = self._pending.pop(cast(JsonlRequestId, raw_id), None)
            if future is None:
                self._incoming.put_nowait(payload)
                return
            if future.done():
                return
            if "error" in payload:
                error = payload["error"]
                code: int | str | None = None
                message = "remote JSONL request failed"
                if isinstance(error, Mapping):
                    raw_code = error.get("code")
                    if isinstance(raw_code, (int, str)) and not isinstance(
                        raw_code, bool
                    ):
                        code = raw_code
                    raw_message = error.get("message")
                    if isinstance(raw_message, str) and raw_message:
                        message = raw_message
                future.set_exception(
                    JsonlRemoteError(
                        request_id=cast(JsonlRequestId, raw_id),
                        code=code,
                        message=message,
                    )
                )
                return
            future.set_result(payload)
            return
        self._incoming.put_nowait(payload)

    async def _protocol_failure(
        self,
        message: str,
        *,
        line_number: int | None = None,
    ) -> None:
        error = JsonlProtocolError(message, line_number=line_number)
        if self._fatal_error is None:
            self._fatal_error = error
        await self._fail_pending(error)
        process = self._process
        if process is not None and process.returncode is None:
            _terminate_process(process)

    def _schedule_protocol_failure(self, message: str) -> None:
        asyncio.create_task(
            self._protocol_failure(message),
            name="codex-app-server-protocol-failure",
        )

    async def _fail_pending(self, error: JsonlTransportError) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    async def _wait_for_process(
        self,
        process: asyncio.subprocess.Process,
        deadline: float,
    ) -> None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            await asyncio.shield(process.wait())

    async def _join_tasks(self) -> None:
        tasks = tuple(
            task
            for task in (self._stdout_task, self._stderr_task, self._watch_task)
            if task is not None and task is not asyncio.current_task()
        )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._stdout_task = None
        self._stderr_task = None
        self._watch_task = None

    def _reset_runtime_state(self) -> None:
        while True:
            try:
                self._incoming.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._stderr_tail.clear()
        self._returncode = None
        self._fatal_error = None
        self._next_request_id = 0
        self._closed_message_sent = False
        self._exit_event.clear()
        self._pending.clear()

    def _send_closed_message(self) -> None:
        if self._closed_message_sent:
            return
        self._closed_message_sent = True
        self._incoming.put_nowait(_QUEUE_CLOSED)

    def _remove_pending(
        self,
        request_id: JsonlRequestId,
        future: asyncio.Future[JsonlMessage],
    ) -> None:
        if self._pending.get(request_id) is future:
            self._pending.pop(request_id, None)
        if not future.done():
            future.cancel()

    def _next_id(self) -> int:
        self._next_request_id += 1
        return self._next_request_id

    def _ensure_running(self) -> None:
        if not self.is_running:
            raise JsonlProcessNotRunning()


def _validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("timeout must be a positive number")
    if timeout <= 0:
        raise ValueError("timeout must be positive")


def _validate_method(method: str) -> None:
    if not isinstance(method, str) or not method:
        raise ValueError("method must be a non-empty string")


def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return


def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        return


__all__ = [
    "JsonlProcessSpec",
    "JsonlProcessState",
    "JsonlProcessSupervisor",
]
