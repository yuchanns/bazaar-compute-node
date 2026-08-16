from __future__ import annotations

from collections.abc import Mapping

import aiohttp

_API_BASE_URL = "https://api.telegram.org"
_POLL_TIMEOUT_SECONDS = 50
_POLL_HTTP_TIMEOUT_SECONDS = 60
_CONNECT_TIMEOUT_SECONDS = 10
_MAX_ERROR_DESCRIPTION = 256


class TelegramApiError(RuntimeError):
    def __init__(
        self,
        method: str,
        *,
        http_status: int,
        error_code: int | None,
        description: str,
        retry_after: int | None = None,
    ) -> None:
        self.method = method
        self.http_status = http_status
        self.error_code = error_code
        self.retry_after = retry_after
        code = error_code if error_code is not None else http_status
        super().__init__(f"Telegram {method} failed ({code}): {description}")


class TelegramTransportError(RuntimeError):
    def __init__(self, method: str, error_type: str) -> None:
        self.method = method
        self.error_type = error_type
        super().__init__(f"Telegram {method} transport failed: {error_type}")


class TelegramBotApi:
    def __init__(self, session: aiohttp.ClientSession, *, token: str) -> None:
        self._session = session
        self._token = token

    async def get_me(self, *, timeout: float) -> Mapping[str, object]:
        result = await self._request("getMe", {}, timeout=timeout)
        if not isinstance(result, Mapping):
            raise TelegramApiError(
                "getMe",
                http_status=200,
                error_code=None,
                description="provider result is not an object",
            )
        return result

    async def get_updates(
        self,
        *,
        offset: int | None,
    ) -> tuple[Mapping[str, object], ...]:
        payload: dict[str, object] = {
            "limit": 100,
            "timeout": _POLL_TIMEOUT_SECONDS,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._request(
            "getUpdates",
            payload,
            timeout=_POLL_HTTP_TIMEOUT_SECONDS,
        )
        if not isinstance(result, list) or not all(
            isinstance(update, Mapping) for update in result
        ):
            raise TelegramApiError(
                "getUpdates",
                http_status=200,
                error_code=None,
                description="provider result is not an array of updates",
            )
        return tuple(dict(update) for update in result)

    async def _request(
        self,
        method: str,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> object:
        if timeout <= 0:
            raise TimeoutError(f"Telegram {method} deadline expired")
        client_timeout = aiohttp.ClientTimeout(
            total=timeout,
            connect=min(timeout, _CONNECT_TIMEOUT_SECONDS),
            sock_connect=min(timeout, _CONNECT_TIMEOUT_SECONDS),
        )
        url = f"{_API_BASE_URL}/bot{self._token}/{method}"
        try:
            async with self._session.post(
                url,
                json=dict(payload),
                timeout=client_timeout,
            ) as response:
                http_status = response.status
                try:
                    body = await response.json(content_type=None)
                except aiohttp.ClientError, ValueError:
                    raise TelegramApiError(
                        method,
                        http_status=http_status,
                        error_code=None,
                        description="provider returned invalid JSON",
                    ) from None
        except TelegramApiError:
            raise
        except TimeoutError:
            raise TelegramTransportError(method, "TimeoutError") from None
        except aiohttp.ClientError as error:
            raise TelegramTransportError(method, type(error).__name__) from None
        except OSError as error:
            raise TelegramTransportError(method, type(error).__name__) from None

        if not isinstance(body, Mapping):
            raise TelegramApiError(
                method,
                http_status=http_status,
                error_code=None,
                description="provider response is not an object",
            )
        if body.get("ok") is True and 200 <= http_status < 300:
            return body.get("result")

        error_code = body.get("error_code")
        if not isinstance(error_code, int) or isinstance(error_code, bool):
            error_code = None
        description = body.get("description")
        if not isinstance(description, str) or not description:
            description = "provider rejected request"
        if self._token in description:
            description = description.replace(self._token, "<redacted>")
        description = description[:_MAX_ERROR_DESCRIPTION]
        parameters = body.get("parameters")
        retry_after: int | None = None
        if isinstance(parameters, Mapping):
            candidate = parameters.get("retry_after")
            if (
                isinstance(candidate, int)
                and not isinstance(candidate, bool)
                and candidate >= 0
            ):
                retry_after = candidate
        raise TelegramApiError(
            method,
            http_status=http_status,
            error_code=error_code,
            description=description,
            retry_after=retry_after,
        )


__all__ = [
    "TelegramApiError",
    "TelegramBotApi",
    "TelegramTransportError",
]
