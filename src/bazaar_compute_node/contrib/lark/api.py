from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from urllib.parse import parse_qs, quote, urlparse

import aiohttp

from ...core.timerwheel import TimerWheel

_CONNECT_TIMEOUT_SECONDS = 10.0
_MAX_ERROR_MESSAGE = 256
_TOKEN_REFRESH_MARGIN_SECONDS = 600.0
_TOKEN_REFRESH_TIMEOUT_SECONDS = 60.0
_TOKEN_RETRY_INITIAL_SECONDS = 1.0
_TOKEN_RETRY_MAX_SECONDS = 60.0
_MIN_INTERVAL = 1
_MAX_INTERVAL = 86_400
_MIN_RECONNECT_COUNT = -1
_MAX_RECONNECT_COUNT = 10_000


class LarkApiError(RuntimeError):
    def __init__(
        self,
        method: str,
        *,
        http_status: int,
        provider_code: int | None,
        message: str,
    ) -> None:
        self.method = method
        self.http_status = http_status
        self.provider_code = provider_code
        self.message = message[:_MAX_ERROR_MESSAGE]
        code = provider_code if provider_code is not None else http_status
        super().__init__(f"Lark {method} failed ({code}): {self.message}")


class LarkTransportError(RuntimeError):
    def __init__(self, method: str, error_kind: str) -> None:
        self.method = method
        self.error_kind = error_kind
        super().__init__(f"Lark {method} transport failed: {error_kind}")


@dataclass(frozen=True, slots=True)
class ClientConfig:
    ping_interval: int = 120
    reconnect_count: int = -1
    reconnect_interval: int = 120
    reconnect_nonce: int = 30

    @classmethod
    def from_payload(cls, payload: object) -> ClientConfig:
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise TypeError("Lark ClientConfig must be an object")
        defaults = cls()
        values = {
            "ping_interval": _bounded_int(
                payload.get("PingInterval", defaults.ping_interval),
                minimum=_MIN_INTERVAL,
                maximum=_MAX_INTERVAL,
                field_name="PingInterval",
            ),
            "reconnect_interval": _bounded_int(
                payload.get("ReconnectInterval", defaults.reconnect_interval),
                minimum=_MIN_INTERVAL,
                maximum=_MAX_INTERVAL,
                field_name="ReconnectInterval",
            ),
            "reconnect_nonce": _bounded_int(
                payload.get("ReconnectNonce", defaults.reconnect_nonce),
                minimum=_MIN_INTERVAL,
                maximum=_MAX_INTERVAL,
                field_name="ReconnectNonce",
            ),
            "reconnect_count": _bounded_int(
                payload.get("ReconnectCount", defaults.reconnect_count),
                minimum=_MIN_RECONNECT_COUNT,
                maximum=_MAX_RECONNECT_COUNT,
                field_name="ReconnectCount",
            ),
        }
        return cls(**values)


@dataclass(frozen=True, slots=True)
class LarkEndpoint:
    url: str
    service_id: int
    device_id: str
    client_config: ClientConfig


@dataclass(frozen=True, slots=True)
class _TokenSnapshot:
    token: str
    expires_at: float
    refresh_at: float


class LarkApi:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        app_id: str,
        app_secret: str,
        base_url: str,
        timer_wheel: TimerWheel | None = None,
    ) -> None:
        self._session = session
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._timer_wheel = timer_wheel
        self._token_snapshot: _TokenSnapshot | None = None
        self._token_refresh_task: asyncio.Task[None] | None = None
        self._token_available = asyncio.Event()
        self._token_refresh_error: Exception | None = None
        self._token_stopping = False
        self._token_refresh_failures = 0

    @property
    def token_refresh_failures(self) -> int:
        return self._token_refresh_failures

    @property
    def session(self) -> aiohttp.ClientSession:
        return self._session

    async def start(self) -> None:
        task = self._token_refresh_task
        if task is not None and not task.done():
            return
        if self._timer_wheel is None:
            raise RuntimeError("Lark token refresh requires a timer wheel")
        self._token_stopping = False
        self._token_snapshot = None
        self._token_refresh_error = None
        self._token_available.clear()
        self._token_refresh_task = asyncio.create_task(
            self._run_token_refresh(),
            name="bcn-lark-token-refresh",
        )

    async def stop(self) -> None:
        self._token_stopping = True
        self._token_available.set()
        task = self._token_refresh_task
        self._token_refresh_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._token_snapshot = None
        self._token_refresh_error = None
        self._token_available.clear()

    async def get_tenant_access_token(self, *, timeout: float) -> str:
        if timeout <= 0:
            raise TimeoutError("Lark tenant token deadline expired")
        if self._token_stopping:
            raise LarkTransportError("tenant_access_token", "stopped")
        await self.start()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if self._token_stopping:
                raise LarkTransportError("tenant_access_token", "stopped")
            snapshot = self._token_snapshot
            if snapshot is not None and monotonic() < snapshot.expires_at:
                return snapshot.token
            if snapshot is not None:
                self._token_available.clear()
                snapshot = self._token_snapshot
                if snapshot is not None and monotonic() < snapshot.expires_at:
                    return snapshot.token
            error = self._token_refresh_error
            if error is not None:
                raise error
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("Lark tenant token deadline expired")
            try:
                await asyncio.wait_for(
                    self._token_available.wait(),
                    timeout=remaining,
                )
            except TimeoutError:
                raise TimeoutError("Lark tenant token deadline expired") from None

    async def _run_token_refresh(self) -> None:
        retry_delay = _TOKEN_RETRY_INITIAL_SECONDS
        while not self._token_stopping:
            try:
                snapshot = await self._refresh_tenant_access_token()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                self._record_token_refresh_failure(error)
                if self._token_stopping:
                    return
                if not await self._wait_for_retry(retry_delay):
                    return
                retry_delay = min(
                    retry_delay * 2,
                    _TOKEN_RETRY_MAX_SECONDS,
                )
                continue

            self._token_snapshot = snapshot
            self._token_refresh_error = None
            self._token_available.set()
            retry_delay = _TOKEN_RETRY_INITIAL_SECONDS
            try:
                await self._wait_for_token_refresh(
                    max(0.0, snapshot.refresh_at - monotonic())
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                self._token_refresh_failures += 1
                self._token_refresh_error = error
                self._token_available.set()
                return

    async def _refresh_tenant_access_token(self) -> _TokenSnapshot:
        issued_at = monotonic()
        body = await self._request_json(
            "tenant_access_token",
            "/open-apis/auth/v3/tenant_access_token/internal",
            timeout=_TOKEN_REFRESH_TIMEOUT_SECONDS,
            json_body={
                "app_id": self._app_id,
                "app_secret": self._app_secret,
            },
        )
        self._check_provider_result(body, "tenant_access_token")
        token = body.get("tenant_access_token")
        expire = body.get("expire")
        if not isinstance(token, str) or not token.strip():
            raise LarkApiError(
                "tenant_access_token",
                http_status=200,
                provider_code=0,
                message="provider response is missing tenant_access_token",
            )
        if not isinstance(expire, int) or isinstance(expire, bool) or expire <= 0:
            raise LarkApiError(
                "tenant_access_token",
                http_status=200,
                provider_code=0,
                message="provider response contains an invalid expiry",
            )
        expires_at = issued_at + expire
        if expires_at <= monotonic():
            raise LarkApiError(
                "tenant_access_token",
                http_status=200,
                provider_code=0,
                message="provider response contains an expired token",
            )
        refresh_margin = min(
            _TOKEN_REFRESH_MARGIN_SECONDS,
            max(1.0, expire / 2),
        )
        return _TokenSnapshot(
            token=token,
            expires_at=expires_at,
            refresh_at=expires_at - refresh_margin,
        )

    def _record_token_refresh_failure(self, error: Exception) -> None:
        self._token_refresh_failures += 1
        snapshot = self._token_snapshot
        if snapshot is None or monotonic() >= snapshot.expires_at:
            self._token_refresh_error = error
            self._token_available.set()

    async def _wait_for_retry(self, delay_seconds: float) -> bool:
        try:
            await self._wait_for_token_refresh(delay_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._record_token_refresh_failure(error)
            self._token_available.set()
            return False
        return True

    async def _wait_for_token_refresh(self, delay_seconds: float) -> None:
        timer_wheel = self._timer_wheel
        if timer_wheel is None:
            raise RuntimeError("Lark token refresh requires a timer wheel")
        delay_ms = min(
            max(1, math.ceil(max(0.0, delay_seconds) * 1_000)),
            timer_wheel.maximum_delay_ms,
        )
        timer = timer_wheel.create(delay_ms)
        await timer.wait()

    async def get_endpoint(self, *, timeout: float) -> LarkEndpoint:
        body = await self._request_json(
            "ws_endpoint",
            "/callback/ws/endpoint",
            timeout=timeout,
            json_body={"AppID": self._app_id, "AppSecret": self._app_secret},
        )
        self._check_provider_result(body, "ws_endpoint")
        data = body.get("data")
        if not isinstance(data, Mapping):
            raise LarkApiError(
                "ws_endpoint",
                http_status=200,
                provider_code=0,
                message="provider response is missing endpoint data",
            )
        url = data.get("URL")
        if not isinstance(url, str) or not url:
            raise LarkApiError(
                "ws_endpoint",
                http_status=200,
                provider_code=0,
                message="provider response is missing endpoint URL",
            )
        parsed = urlparse(url)
        if parsed.scheme != "wss" or not parsed.netloc:
            raise LarkApiError(
                "ws_endpoint",
                http_status=200,
                provider_code=0,
                message="provider endpoint URL is invalid",
            )
        query = parse_qs(parsed.query, keep_blank_values=True)
        service_id = _parse_service_id(query.get("service_id"))
        device_id = _parse_query_text(query.get("device_id"), "device_id")
        client_config = ClientConfig.from_payload(data.get("ClientConfig"))
        return LarkEndpoint(
            url=url,
            service_id=service_id,
            device_id=device_id,
            client_config=client_config,
        )

    async def get_bot_info(self, *, timeout: float) -> Mapping[str, object]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        token = await self.get_tenant_access_token(
            timeout=max(0.0, deadline - loop.time())
        )
        body = await self._request_json(
            "bot_info",
            "/open-apis/bot/v3/info",
            timeout=max(0.0, deadline - loop.time()),
            headers={"Authorization": f"Bearer {token}"},
        )
        self._check_provider_result(body, "bot_info")
        return body

    async def get_user(
        self,
        user_id: str,
        *,
        timeout: float,
    ) -> Mapping[str, object]:
        return await self._get_json(
            "contact_user",
            f"/open-apis/contact/v3/users/{quote(user_id, safe='')}",
            timeout=timeout,
            params={"user_id_type": "open_id"},
        )

    async def get_message(
        self,
        message_id: str,
        *,
        timeout: float,
    ) -> Mapping[str, object]:
        return await self._get_json(
            "message_get",
            f"/open-apis/im/v1/messages/{quote(message_id, safe='')}",
            timeout=timeout,
            params={"user_id_type": "open_id"},
        )

    @asynccontextmanager
    async def open_message_resource(
        self,
        message_id: str,
        file_key: str,
        resource_type: str,
        *,
        timeout: float,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        if timeout <= 0:
            raise TimeoutError("Lark message resource deadline expired")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        token = await self.get_tenant_access_token(
            timeout=max(0.0, deadline - loop.time())
        )
        remaining = max(0.0, deadline - loop.time())
        if remaining <= 0:
            raise TimeoutError("Lark message resource deadline expired")
        client_timeout = aiohttp.ClientTimeout(
            total=remaining,
            connect=min(remaining, _CONNECT_TIMEOUT_SECONDS),
            sock_connect=min(remaining, _CONNECT_TIMEOUT_SECONDS),
        )
        url = (
            f"{self._base_url}/open-apis/im/v1/messages/"
            f"{quote(message_id, safe='')}/resources/{quote(file_key, safe='')}"
        )
        try:
            request = self._session.get(
                url,
                params={"type": resource_type},
                headers={"Authorization": f"Bearer {token}"},
                timeout=client_timeout,
            )
            async with request as response:
                status = response.status
                if status < 200 or status >= 300:
                    raise LarkApiError(
                        "message_resource",
                        http_status=status,
                        provider_code=None,
                        message="provider rejected message resource",
                    )
                yield response
        except LarkApiError:
            raise
        except TimeoutError:
            raise LarkTransportError("message_resource", "TimeoutError") from None
        except (aiohttp.ClientError, OSError) as error:
            raise LarkTransportError("message_resource", type(error).__name__) from None

    async def _get_json(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        token = await self.get_tenant_access_token(
            timeout=max(0.0, deadline - loop.time())
        )
        body = await self._request_json(
            method,
            path,
            timeout=max(0.0, deadline - loop.time()),
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        self._check_provider_result(body, method)
        return body

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
        json_body: dict[str, object] | None = None,
        params: Mapping[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Mapping[str, object]:
        if timeout <= 0:
            raise TimeoutError(f"Lark {method} deadline expired")
        client_timeout = aiohttp.ClientTimeout(
            total=timeout,
            connect=min(timeout, _CONNECT_TIMEOUT_SECONDS),
            sock_connect=min(timeout, _CONNECT_TIMEOUT_SECONDS),
        )
        url = f"{self._base_url}{path}"
        try:
            if json_body is None:
                request = self._session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=client_timeout,
                )
            else:
                request = self._session.post(
                    url,
                    json=json_body,
                    params=params,
                    headers=headers,
                    timeout=client_timeout,
                )
            async with request as response:
                status = response.status
                try:
                    raw_body = await response.json(content_type=None)
                except aiohttp.ClientError:
                    raise LarkApiError(
                        method,
                        http_status=status,
                        provider_code=None,
                        message="provider returned invalid JSON",
                    ) from None
                except ValueError:
                    raise LarkApiError(
                        method,
                        http_status=status,
                        provider_code=None,
                        message="provider returned invalid JSON",
                    ) from None
        except LarkApiError:
            raise
        except TimeoutError:
            raise LarkTransportError(method, "TimeoutError") from None
        except (aiohttp.ClientError, OSError) as error:
            raise LarkTransportError(method, type(error).__name__) from None

        if not isinstance(raw_body, dict):
            raise LarkApiError(
                method,
                http_status=status,
                provider_code=None,
                message="provider response is not an object",
            )
        if status < 200 or status >= 300:
            raise LarkApiError(
                method,
                http_status=status,
                provider_code=_provider_code(raw_body.get("code")),
                message=self._safe_provider_message(raw_body.get("msg")),
            )
        return raw_body

    def _check_provider_result(self, body: Mapping[str, object], method: str) -> None:
        code = _provider_code(body.get("code"))
        if code != 0:
            raise LarkApiError(
                method,
                http_status=200,
                provider_code=code,
                message=self._safe_provider_message(body.get("msg")),
            )

    def _safe_provider_message(self, value: object) -> str:
        message = _provider_message(value)
        snapshot = self._token_snapshot
        for secret in (
            self._app_secret,
            snapshot.token if snapshot is not None else None,
        ):
            if secret:
                message = message.replace(secret, "<redacted>")
        return message


def _bounded_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    field_name: str,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Lark {field_name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"Lark {field_name} is outside the allowed range")
    return value


def _parse_service_id(values: list[str] | None) -> int:
    value = _parse_query_text(values, "service_id")
    try:
        parsed = int(value)
    except ValueError:
        raise ValueError("Lark service_id must be an integer") from None
    if parsed < 0:
        raise ValueError("Lark service_id must be non-negative")
    return parsed


def _parse_query_text(values: list[str] | None, field_name: str) -> str:
    if values is None or len(values) != 1 or not values[0]:
        raise ValueError(f"Lark endpoint is missing {field_name}")
    value = values[0]
    if "\r" in value or "\n" in value:
        raise ValueError(f"Lark endpoint {field_name} contains a line break")
    return value


def _provider_code(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _provider_message(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "provider rejected request"
    return value[:_MAX_ERROR_MESSAGE]


__all__ = [
    "ClientConfig",
    "LarkApi",
    "LarkApiError",
    "LarkEndpoint",
    "LarkTransportError",
]
