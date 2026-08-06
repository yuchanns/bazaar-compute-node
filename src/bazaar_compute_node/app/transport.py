from __future__ import annotations

import asyncio
import json
import os
import secrets
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

RequestHandler = Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]


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
        self._capability: str | None = None
        self._endpoint: str | None = None

    def set_handler(self, handler: RequestHandler) -> None:
        if self._server is not None:
            raise RuntimeError("local command server handler is already active")
        self._handler = handler

    @property
    def endpoint(self) -> str:
        if self._endpoint is None:
            raise RuntimeError("local command server is not started")
        return self._endpoint

    async def start(self) -> None:
        if self._server is not None:
            return

        if os.name == "nt":
            self._capability = secrets.token_urlsafe(24)
            self._server = await asyncio.start_server(
                self._handle_client,
                host="127.0.0.1",
                port=0,
            )
            sockets = self._server.sockets
            if not sockets:
                raise RuntimeError("local command server did not expose a socket")
            address = sockets[0].getsockname()
            self._endpoint = (
                f"tcp://127.0.0.1:{address[1]}?{urlencode({'token': self._capability})}"
            )
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
        self._endpoint = f"unix://{path}"

    async def stop(self) -> None:
        server = self._server
        self._server = None
        self._endpoint = None
        self._capability = None
        if server is not None:
            server.close()
            await server.wait_closed()
        path = self._unix_path
        self._unix_path = None
        if path is not None and path.exists():
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
            if self._handler is None:
                raise RuntimeError("local command server is not ready")
            if self._capability is not None:
                capability = payload.pop("capability", None)
                if capability != self._capability:
                    response: Mapping[str, object] = {
                        "ok": False,
                        "code": "LOCAL_AUTH_FAILED",
                        "error": "local command capability is invalid",
                    }
                else:
                    response = await self._handler(payload)
            else:
                response = await self._handler(payload)
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
        if parsed.scheme == "unix":
            reader, writer = await asyncio.open_unix_connection(parsed.path)
        elif parsed.scheme == "tcp":
            query = parse_qs(parsed.query)
            token_values = query.get("token")
            if not token_values:
                raise ValueError("TCP command endpoint has no capability token")
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
