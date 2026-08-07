from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

RequestHandler = Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]
StreamPair = tuple[asyncio.StreamReader, asyncio.StreamWriter]


def local_endpoint_for_path(endpoint_path: Path) -> str:
    """Return the stable local endpoint represented by one configured path."""

    path = endpoint_path.expanduser()
    if sys.platform == "win32":
        from .windows_pipe import named_pipe_endpoint

        return named_pipe_endpoint(path)
    return f"unix://{path}"


if sys.platform == "win32":

    async def _open_unix_connection(path: str) -> StreamPair:
        raise ValueError(f"Unix command endpoints are not supported on Windows: {path}")

else:

    async def _open_unix_connection(path: str) -> StreamPair:
        return await asyncio.open_unix_connection(path)


class LocalCommandServer:
    """Serve one request per local JSONL connection."""

    def __init__(
        self,
        handler: RequestHandler | None = None,
        *,
        endpoint_path: Path | None = None,
    ) -> None:
        self._handler = handler
        self._endpoint_path = endpoint_path
        self._server: asyncio.AbstractServer | None = None
        self._unix_path: Path | None = None
        self._unix_identity: tuple[int, int] | None = None
        self._windows_server: Any | None = None
        self._capability: str | None = None
        self._endpoint: str | None = None

    def set_handler(self, handler: RequestHandler) -> None:
        if self._server is not None or self._windows_server is not None:
            raise RuntimeError("local command server handler is already active")
        self._handler = handler

    @property
    def endpoint(self) -> str:
        if self._endpoint is None:
            raise RuntimeError("local command server is not started")
        return self._endpoint

    async def start(self) -> None:
        if self._server is not None or self._windows_server is not None:
            return

        if sys.platform == "win32":
            from .windows_pipe import WindowsNamedPipeServer

            windows_server = WindowsNamedPipeServer(
                self._dispatch,
                endpoint_path=self._endpoint_path,
            )
            await windows_server.start()
            self._windows_server = windows_server
            self._endpoint = windows_server.endpoint
            return

        path = self._endpoint_path
        if path is None:
            path = (
                Path(tempfile.gettempdir())
                / f"bcn-{os.getpid()}-{secrets.token_hex(6)}.sock"
            )
        path = path.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"local command endpoint already exists: {path}")
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(path),
        )
        os.chmod(path, 0o600)
        self._unix_path = path
        path_stat = path.stat()
        self._unix_identity = (path_stat.st_dev, path_stat.st_ino)
        self._endpoint = f"unix://{path}"

    async def stop(self) -> None:
        windows_server = self._windows_server
        self._windows_server = None
        if windows_server is not None:
            await windows_server.stop()
            self._endpoint = None
            self._capability = None
            return

        server = self._server
        self._server = None
        self._endpoint = None
        self._capability = None
        if server is not None:
            server.close()
            await server.wait_closed()
        path = self._unix_path
        self._unix_path = None
        identity = self._unix_identity
        self._unix_identity = None
        if path is not None and identity is not None and path.exists():
            path_stat = path.stat()
            if (path_stat.st_dev, path_stat.st_ino) == identity:
                path.unlink()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("request must be a JSON object")
            response = await self._dispatch(payload)
        except asyncio.CancelledError:
            raise
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
                "error": str(error),
            }
        try:
            writer.write(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()
                + b"\n"
            )
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _dispatch(self, payload: dict[str, object]) -> Mapping[str, object]:
        if self._handler is None:
            raise RuntimeError("local command server is not ready")
        if self._capability is not None:
            capability = payload.pop("capability", None)
            if capability != self._capability:
                return {
                    "ok": False,
                    "code": "LOCAL_AUTH_FAILED",
                    "error": "local command capability is invalid",
                }
        return await self._handler(payload)


class LocalCommandClient:
    """Open a fresh local connection for each command."""

    @staticmethod
    async def request(
        endpoint: str,
        payload: Mapping[str, object],
        *,
        timeout: float = 10,
    ) -> Mapping[str, object]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        return await asyncio.wait_for(
            LocalCommandClient._request(endpoint, payload),
            timeout=timeout,
        )

    @staticmethod
    async def _request(
        endpoint: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        parsed = urlsplit(endpoint)
        request = dict(payload)
        if parsed.scheme == "pipe":
            if sys.platform != "win32":
                raise ValueError(
                    "Windows named pipe endpoints are not supported on this platform"
                )
            from .windows_pipe import request_named_pipe

            return await asyncio.to_thread(request_named_pipe, endpoint, payload)
        if parsed.scheme == "unix":
            reader, writer = await _open_unix_connection(parsed.path)
        elif parsed.scheme == "tcp":
            query = parse_qs(parsed.query)
            token_values = query.get("token")
            if (
                set(query) != {"token"}
                or token_values is None
                or len(token_values) != 1
            ):
                raise ValueError("TCP command endpoint has no capability token")
            if parsed.hostname != "127.0.0.1":
                raise ValueError("TCP command endpoint must use loopback")
            request["capability"] = token_values[0]
            if parsed.hostname is None or parsed.port is None:
                raise ValueError("TCP command endpoint is invalid")
            reader, writer = await asyncio.open_connection(
                parsed.hostname,
                parsed.port,
            )
        else:
            raise ValueError(f"unsupported local command endpoint: {endpoint}")

        try:
            writer.write(
                json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
                + b"\n"
            )
            await writer.drain()
            line = await reader.readline()
            if not line:
                raise ConnectionError("local command server closed without a response")
            response = json.loads(line)
            if not isinstance(response, dict):
                raise TypeError("local command response must be a JSON object")
            return response
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
