from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from time import time_ns
from typing import Any, cast

if sys.platform == "win32":
    from ctypes import wintypes

    _ERROR_ACCESS_DENIED = 5
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _open_process = _kernel32.OpenProcess
    _open_process.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    _open_process.restype = wintypes.HANDLE
    _get_exit_code_process = _kernel32.GetExitCodeProcess
    _get_exit_code_process.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    _get_exit_code_process.restype = wintypes.BOOL
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = (wintypes.HANDLE,)
    _close_handle.restype = wintypes.BOOL

    def _process_is_alive_windows(pid: int) -> bool:
        handle = _open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            exit_code = wintypes.DWORD()
            if not _get_exit_code_process(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == _STILL_ACTIVE
        finally:
            _close_handle(handle)

else:

    def _process_is_alive_windows(pid: int) -> bool:
        raise RuntimeError("Windows process probing is unavailable on this platform")


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    """Persistent local metadata needed to control one node process."""

    pid: int
    endpoint: str
    channel_slug: str
    runtime_slug: str
    storage_slug: str
    audit_slug: str
    started_at_ms: int

    def as_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "endpoint": self.endpoint,
            "channel": self.channel_slug,
            "runtime": self.runtime_slug,
            "storage": self.storage_slug,
            "audit": self.audit_slug,
            "started_at_ms": self.started_at_ms,
        }

    @classmethod
    def from_dict(cls, payload: object) -> RuntimeMetadata:
        if not isinstance(payload, dict):
            raise TypeError("runtime metadata must be a JSON object")
        values = {
            "pid": payload.get("pid"),
            "endpoint": payload.get("endpoint"),
            "channel_slug": payload.get("channel"),
            "runtime_slug": payload.get("runtime"),
            "storage_slug": payload.get("storage"),
            "audit_slug": payload.get("audit"),
            "started_at_ms": payload.get("started_at_ms"),
        }
        pid = values["pid"]
        started_at_ms = values["started_at_ms"]
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("runtime metadata pid is invalid")
        if (
            isinstance(started_at_ms, bool)
            or not isinstance(started_at_ms, int)
            or started_at_ms < 0
        ):
            raise ValueError("runtime metadata started_at_ms is invalid")
        for value, field_name in (
            (values["endpoint"], "endpoint"),
            (values["channel_slug"], "channel"),
            (values["runtime_slug"], "runtime"),
            (values["storage_slug"], "storage"),
            (values["audit_slug"], "audit"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"runtime metadata {field_name} is invalid")
        return cls(
            pid=pid,
            endpoint=cast(str, values["endpoint"]),
            channel_slug=cast(str, values["channel_slug"]),
            runtime_slug=cast(str, values["runtime_slug"]),
            storage_slug=cast(str, values["storage_slug"]),
            audit_slug=cast(str, values["audit_slug"]),
            started_at_ms=started_at_ms,
        )


def write_runtime_metadata(path: Path, metadata: RuntimeMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(
        json.dumps(metadata.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def read_runtime_metadata(path: Path) -> RuntimeMetadata | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RuntimeMetadata.from_dict(payload)
    except FileNotFoundError:
        return None
    except (OSError, TypeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"runtime metadata cannot be read: {error}") from error


def remove_runtime_metadata(path: Path, *, pid: int | None = None) -> None:
    try:
        metadata = read_runtime_metadata(path)
    except RuntimeError:
        metadata = None
    if metadata is not None and pid is not None and metadata.pid != pid:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _process_is_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def wait_for_runtime_metadata(
    path: Path,
    process: Any,
    *,
    timeout: float,
) -> RuntimeMetadata:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        metadata = read_runtime_metadata(path)
        if metadata is not None:
            return metadata
        if process.poll() is not None:
            raise RuntimeError(
                f"daemon exited before becoming ready; see {path.with_name('bcn.log')}"
            )
        await asyncio.sleep(0.05)
    raise TimeoutError(f"daemon did not become ready within {timeout:g} seconds")


async def wait_for_process_exit(pid: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return True
        await asyncio.sleep(0.05)
    return not process_is_alive(pid)


def new_runtime_metadata(
    *,
    endpoint: str,
    channel_slug: str,
    runtime_slug: str,
    storage_slug: str,
    audit_slug: str,
) -> RuntimeMetadata:
    return RuntimeMetadata(
        pid=os.getpid(),
        endpoint=endpoint,
        channel_slug=channel_slug,
        runtime_slug=runtime_slug,
        storage_slug=storage_slug,
        audit_slug=audit_slug,
        started_at_ms=time_ns() // 1_000_000,
    )


__all__ = [
    "RuntimeMetadata",
    "new_runtime_metadata",
    "process_is_alive",
    "read_runtime_metadata",
    "remove_runtime_metadata",
    "wait_for_process_exit",
    "wait_for_runtime_metadata",
    "write_runtime_metadata",
]
