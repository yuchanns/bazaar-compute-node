from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bazaar_compute_node.core.lifecycle import TaskFailureSignal
from bazaar_compute_node.core.paths import resolve_data_dir

from ..core.utils.text import format_exception

RequestHandler = Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]

_ERROR_ALREADY_EXISTS = 183
_ERROR_BROKEN_PIPE = 109
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PIPE_BUSY = 231
_ERROR_PIPE_CONNECTED = 535
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_PIPE_ACCESS_DUPLEX = 0x00000003
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_READMODE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_PIPE_UNLIMITED_INSTANCES = 255
_PIPE_BUFFER_SIZE = 64 * 1024
_MAX_MESSAGE_SIZE = 1024 * 1024
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_WinDLL = getattr(ctypes, "WinDLL")  # noqa: B009
_get_last_error = getattr(ctypes, "get_last_error")  # noqa: B009
_set_last_error = getattr(ctypes, "set_last_error")  # noqa: B009
_kernel32 = _WinDLL("kernel32", use_last_error=True)

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = (wintypes.HANDLE,)
_CloseHandle.restype = wintypes.BOOL

_ConnectNamedPipe = _kernel32.ConnectNamedPipe
_ConnectNamedPipe.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
_ConnectNamedPipe.restype = wintypes.BOOL

_CreateFileW = _kernel32.CreateFileW
_CreateFileW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
)
_CreateFileW.restype = wintypes.HANDLE

_CreateMutexW = _kernel32.CreateMutexW
_CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_CreateMutexW.restype = wintypes.HANDLE

_CreateNamedPipeW = _kernel32.CreateNamedPipeW
_CreateNamedPipeW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
)
_CreateNamedPipeW.restype = wintypes.HANDLE

_DisconnectNamedPipe = _kernel32.DisconnectNamedPipe
_DisconnectNamedPipe.argtypes = (wintypes.HANDLE,)
_DisconnectNamedPipe.restype = wintypes.BOOL

_FlushFileBuffers = _kernel32.FlushFileBuffers
_FlushFileBuffers.argtypes = (wintypes.HANDLE,)
_FlushFileBuffers.restype = wintypes.BOOL

_ReadFile = _kernel32.ReadFile
_ReadFile.argtypes = (
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
)
_ReadFile.restype = wintypes.BOOL

_ReleaseMutex = _kernel32.ReleaseMutex
_ReleaseMutex.argtypes = (wintypes.HANDLE,)
_ReleaseMutex.restype = wintypes.BOOL

_WaitNamedPipeW = _kernel32.WaitNamedPipeW
_WaitNamedPipeW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
_WaitNamedPipeW.restype = wintypes.BOOL

_WriteFile = _kernel32.WriteFile
_WriteFile.argtypes = (
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
)
_WriteFile.restype = wintypes.BOOL


def _raise_last_error(message: str) -> None:
    error_code = _get_last_error()
    raise OSError(error_code, f"{message} (WinError {error_code})")


def _is_invalid_handle(handle: Any) -> bool:
    return handle is None or handle == _INVALID_HANDLE_VALUE


def _pipe_name(endpoint_path: Path | None) -> str:
    path = endpoint_path or (resolve_data_dir() / "bcn.sock")
    identity = str(path.expanduser().absolute()).casefold()
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"bcn-{digest}"


def named_pipe_endpoint(endpoint_path: Path | None) -> str:
    return f"pipe://{_pipe_name(endpoint_path)}"


def _pipe_path(pipe_name: str) -> str:
    return rf"\\.\pipe\{pipe_name}"


def _create_mutex(pipe_name: str) -> Any:
    _set_last_error(0)
    handle = _CreateMutexW(None, True, rf"Local\{pipe_name}-mutex")
    if _is_invalid_handle(handle):
        _raise_last_error("CreateMutexW failed")
    if _get_last_error() == _ERROR_ALREADY_EXISTS:
        _CloseHandle(handle)
        raise FileExistsError(f"Windows named mutex already exists: {pipe_name}")
    return handle


def _release_mutex(handle: Any) -> None:
    if _is_invalid_handle(handle):
        return
    _ReleaseMutex(handle)
    _CloseHandle(handle)


def _create_named_pipe(pipe_name: str) -> Any:
    handle = _CreateNamedPipeW(
        _pipe_path(pipe_name),
        _PIPE_ACCESS_DUPLEX,
        _PIPE_TYPE_BYTE
        | _PIPE_READMODE_BYTE
        | _PIPE_WAIT
        | _PIPE_REJECT_REMOTE_CLIENTS,
        _PIPE_UNLIMITED_INSTANCES,
        _PIPE_BUFFER_SIZE,
        _PIPE_BUFFER_SIZE,
        0,
        None,
    )
    if _is_invalid_handle(handle):
        _raise_last_error("CreateNamedPipeW failed")
    return handle


def _open_named_pipe(pipe_name: str) -> Any:
    path = _pipe_path(pipe_name)
    for _ in range(20):
        handle = _CreateFileW(
            path,
            _GENERIC_READ | _GENERIC_WRITE,
            0,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if not _is_invalid_handle(handle):
            return handle
        error_code = _get_last_error()
        if error_code != _ERROR_PIPE_BUSY:
            if error_code == _ERROR_FILE_NOT_FOUND:
                raise FileNotFoundError(path)
            _raise_last_error("CreateFileW for named pipe failed")
        if not _WaitNamedPipeW(path, 100):
            _raise_last_error("WaitNamedPipeW failed")
    raise TimeoutError(f"named pipe did not become available: {path}")


def _read_message(handle: Any) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size <= _MAX_MESSAGE_SIZE:
        buffer = ctypes.create_string_buffer(4096)
        count = wintypes.DWORD()
        if not _ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
            error_code = _get_last_error()
            if error_code == _ERROR_BROKEN_PIPE:
                return b""
            _raise_last_error("ReadFile from named pipe failed")
        if count.value == 0:
            return b""
        chunk = buffer.raw[: count.value]
        chunks.append(chunk)
        size += len(chunk)
        payload = b"".join(chunks)
        line_end = payload.find(b"\n")
        if line_end >= 0:
            return payload[:line_end]
    raise ValueError("named pipe request is too large")


def _write_message(handle: Any, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        buffer = ctypes.create_string_buffer(payload[offset:])
        count = wintypes.DWORD()
        if not _WriteFile(
            handle,
            buffer,
            len(payload) - offset,
            ctypes.byref(count),
            None,
        ):
            _raise_last_error("WriteFile to named pipe failed")
        if count.value == 0:
            raise ConnectionError("named pipe accepted no response bytes")
        offset += count.value


def request_named_pipe(
    endpoint: str, payload: Mapping[str, object]
) -> Mapping[str, object]:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "pipe"
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"invalid Windows named pipe endpoint: {endpoint}")
    handle = _open_named_pipe(parsed.netloc)
    try:
        request = (
            json.dumps(
                dict(payload), ensure_ascii=False, separators=(",", ":")
            ).encode()
            + b"\n"
        )
        _write_message(handle, request)
        response_line = _read_message(handle)
        if not response_line:
            raise ConnectionError("named pipe closed without a response")
        response = json.loads(response_line)
        if not isinstance(response, dict):
            raise TypeError("named pipe response must be a JSON object")
        return response
    finally:
        _CloseHandle(handle)


def _wake_named_pipe(pipe_name: str) -> None:
    try:
        handle = _open_named_pipe(pipe_name)
    except FileNotFoundError, OSError, TimeoutError:
        return
    _CloseHandle(handle)


class WindowsNamedPipeServer:
    """Run one JSONL request handler over a per-user named pipe."""

    def __init__(
        self,
        handler: RequestHandler,
        *,
        endpoint_path: Path | None,
    ) -> None:
        self._handler = handler
        self._pipe_name = _pipe_name(endpoint_path)
        self._endpoint = named_pipe_endpoint(endpoint_path)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mutex: Any = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._startup_errors: list[BaseException] = []
        self._failure = TaskFailureSignal()
        self._thread: threading.Thread | None = None
        self._workers: set[threading.Thread] = set()
        self._workers_lock = threading.Lock()

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def start(self) -> None:
        if self._thread is not None:
            return
        self._mutex = _create_mutex(self._pipe_name)
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._ready.clear()
        self._startup_errors.clear()
        self._failure.reset()
        self._thread = threading.Thread(
            target=self._serve,
            name="bcn-windows-named-pipe",
            daemon=True,
        )
        self._thread.start()
        ready = await asyncio.to_thread(self._ready.wait, 5)
        if not ready or self._startup_errors:
            error = self._startup_errors[0] if self._startup_errors else None
            await self.stop()
            if error is not None:
                raise RuntimeError("Windows named pipe server failed") from error
            raise TimeoutError("Windows named pipe server did not become ready")

    async def stop(self) -> None:
        self._failure.disable()
        thread = self._thread
        if thread is None:
            _release_mutex(self._mutex)
            self._mutex = None
            return
        self._stop.set()
        await asyncio.to_thread(_wake_named_pipe, self._pipe_name)
        await asyncio.to_thread(thread.join, 5)
        await asyncio.to_thread(self._join_workers, 5)
        self._thread = None
        self._loop = None
        _release_mutex(self._mutex)
        self._mutex = None
        if thread.is_alive() or self._live_workers():
            raise TimeoutError("Windows named pipe server did not stop")

    def _serve(self) -> None:
        try:
            self._serve_loop()
        except Exception as error:  # noqa: BLE001
            if not self._ready.is_set():
                self._startup_errors.append(error)
                self._ready.set()
                return
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(self._failure.fail, error)

    async def wait_failure(self) -> None:
        await self._failure.wait()

    def _serve_loop(self) -> None:
        initialized = False
        while not self._stop.is_set():
            handle = _create_named_pipe(self._pipe_name)
            if not initialized:
                initialized = True
                self._ready.set()
            connected = _ConnectNamedPipe(handle, None)
            if not connected and _get_last_error() != _ERROR_PIPE_CONNECTED:
                if self._stop.is_set():
                    _DisconnectNamedPipe(handle)
                    _CloseHandle(handle)
                    return
                _DisconnectNamedPipe(handle)
                _CloseHandle(handle)
                _raise_last_error("ConnectNamedPipe failed")
            if self._stop.is_set():
                _DisconnectNamedPipe(handle)
                _CloseHandle(handle)
                return
            worker = threading.Thread(
                target=self._serve_client_worker,
                args=(handle,),
                name="bcn-windows-named-pipe-client",
                daemon=True,
            )
            with self._workers_lock:
                self._workers.add(worker)
            try:
                worker.start()
            except BaseException:
                with self._workers_lock:
                    self._workers.discard(worker)
                _DisconnectNamedPipe(handle)
                _CloseHandle(handle)
                raise

    def _serve_client_worker(self, handle: Any) -> None:
        try:
            self._serve_client(handle)
        except Exception:  # noqa: BLE001
            return
        finally:
            _DisconnectNamedPipe(handle)
            _CloseHandle(handle)
            with self._workers_lock:
                self._workers.discard(threading.current_thread())

    def _live_workers(self) -> tuple[threading.Thread, ...]:
        with self._workers_lock:
            return tuple(worker for worker in self._workers if worker.is_alive())

    def _join_workers(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        for worker in self._live_workers():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(remaining)

    def _serve_client(self, handle: Any) -> None:
        raw_request = _read_message(handle)
        if not raw_request:
            return
        try:
            payload = json.loads(raw_request)
            if not isinstance(payload, dict):
                raise TypeError("request must be a JSON object")
            loop = self._loop
            if loop is None:
                raise RuntimeError("named pipe server event loop is unavailable")

            async def invoke_handler() -> Mapping[str, object]:
                return await self._handler(payload)

            future = asyncio.run_coroutine_threadsafe(invoke_handler(), loop)
            response: Mapping[str, object] = future.result()
        except json.JSONDecodeError as error:
            response = {
                "ok": False,
                "code": "INVALID_REQUEST",
                "error": f"invalid JSON request: {error.msg}",
            }
        except ValueError as error:
            response = {
                "ok": False,
                "code": "INVALID_REQUEST",
                "error": str(error),
            }
        except Exception as error:  # noqa: BLE001
            response = {
                "ok": False,
                "code": "COMMAND_FAILED",
                "error": format_exception(error),
            }
        encoded_response = (
            json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()
            + b"\n"
        )
        _write_message(handle, encoded_response)
        _FlushFileBuffers(handle)


__all__ = [
    "WindowsNamedPipeServer",
    "named_pipe_endpoint",
    "request_named_pipe",
]
