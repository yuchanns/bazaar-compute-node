from __future__ import annotations

from collections.abc import Mapping

type JsonlMessage = dict[str, object]
type JsonlRequestId = int | str


class JsonlTransportError(RuntimeError):
    """Base error for the adapter-local JSONL process boundary."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


class JsonlProcessNotRunning(JsonlTransportError):
    def __init__(self) -> None:
        super().__init__(
            "JSONL process is not running",
            kind="process_not_running",
        )


class JsonlProcessExited(JsonlTransportError):
    def __init__(
        self,
        *,
        returncode: int | None,
        stderr_tail: tuple[str, ...] = (),
    ) -> None:
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        returncode_text = "unknown" if returncode is None else str(returncode)
        super().__init__(
            f"JSONL process exited with return code {returncode_text}",
            kind="process_exited",
        )


class JsonlProtocolError(JsonlTransportError):
    def __init__(self, message: str, *, line_number: int | None = None) -> None:
        self.line_number = line_number
        super().__init__(message, kind="protocol_error")


class JsonlRequestTimeout(TimeoutError, JsonlTransportError):
    def __init__(self, *, request_id: JsonlRequestId, method: str) -> None:
        self.request_id = request_id
        self.method = method
        TimeoutError.__init__(
            self,
            f"JSONL request {method!r} timed out for id {request_id!r}",
        )
        self.kind = "request_timeout"


class JsonlRemoteError(JsonlTransportError):
    def __init__(
        self,
        *,
        request_id: JsonlRequestId,
        code: int | str | None,
        message: str,
    ) -> None:
        self.request_id = request_id
        self.code = code
        self.remote_message = message
        super().__init__(
            f"JSONL request {request_id!r} failed: {message}",
            kind="remote_error",
        )


def validate_message(payload: Mapping[str, object]) -> JsonlMessage:
    """Copy a mapping into the transport's provider-local message shape."""

    if not isinstance(payload, Mapping):
        raise TypeError("JSONL message must be a mapping")
    return dict(payload)


def is_request_id(value: object) -> bool:
    return isinstance(value, (int, str)) and not isinstance(value, bool)


__all__ = [
    "JsonlMessage",
    "JsonlProcessExited",
    "JsonlProcessNotRunning",
    "JsonlProtocolError",
    "JsonlRemoteError",
    "JsonlRequestId",
    "JsonlRequestTimeout",
    "JsonlTransportError",
    "is_request_id",
    "validate_message",
]
