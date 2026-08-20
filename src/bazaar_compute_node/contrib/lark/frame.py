from __future__ import annotations

from collections.abc import Iterable

import protobug

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_HEADERS = 64
MAX_HEADER_KEY_BYTES = 64
MAX_HEADER_VALUE_BYTES = 4096


class FrameDecodeError(ValueError):
    """Raised when a provider frame violates the bounded wire contract."""


@protobug.message
class Header:
    key: protobug.String = protobug.field(1)
    value: protobug.String = protobug.field(2)


@protobug.message
class Frame:
    SeqID: protobug.UInt64 = protobug.field(1)
    LogID: protobug.UInt64 = protobug.field(2)
    service: protobug.Int32 = protobug.field(3)
    method: protobug.Int32 = protobug.field(4)
    headers: list[Header] = protobug.field(5, default_factory=list)
    payload_encoding: protobug.String | None = protobug.field(6, default=None)
    payload_type: protobug.String | None = protobug.field(7, default=None)
    payload: protobug.Bytes | None = protobug.field(8, default=None)
    LogIDNew: protobug.String | None = protobug.field(9, default=None)


def encode_frame(frame: Frame) -> bytes:
    validate_frame(frame)
    try:
        encoded = protobug.dumps(frame)
    except Exception as error:  # pragma: no cover - defensive library boundary
        raise FrameDecodeError("frame encoding failed") from error
    if len(encoded) > MAX_FRAME_BYTES:
        raise FrameDecodeError("frame exceeds the size limit")
    return encoded


def decode_frame(raw: object) -> Frame:
    if not isinstance(raw, bytes):
        raise FrameDecodeError("frame payload must be bytes")
    if not raw:
        raise FrameDecodeError("frame payload is empty")
    if len(raw) > MAX_FRAME_BYTES:
        raise FrameDecodeError("frame exceeds the size limit")
    try:
        frame = protobug.loads(raw, Frame)
    except Exception as error:
        raise FrameDecodeError("frame decoding failed") from error
    validate_frame(frame)
    return frame


def validate_frame(frame: object) -> None:
    if not isinstance(frame, Frame):
        raise FrameDecodeError("frame has an invalid type")
    if not isinstance(frame.headers, list):
        raise FrameDecodeError("frame headers have an invalid type")
    if len(frame.headers) > MAX_HEADERS:
        raise FrameDecodeError("frame contains too many headers")
    for header in frame.headers:
        if not isinstance(header, Header):
            raise FrameDecodeError("frame contains an invalid header")
        _validate_header_text(header.key, MAX_HEADER_KEY_BYTES, "header key")
        _validate_header_text(header.value, MAX_HEADER_VALUE_BYTES, "header value")
    if frame.payload is not None and len(frame.payload) > MAX_FRAME_BYTES:
        raise FrameDecodeError("frame payload exceeds the size limit")


def header_values(headers: Iterable[Header], key: str) -> tuple[str, ...]:
    if not isinstance(key, str) or not key:
        raise ValueError("header key must be non-empty text")
    return tuple(header.value for header in headers if header.key == key)


def _validate_header_text(value: object, limit: int, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise FrameDecodeError(f"{field_name} must be non-empty text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise FrameDecodeError(f"{field_name} is not valid UTF-8") from error
    if size > limit:
        raise FrameDecodeError(f"{field_name} exceeds the size limit")


__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_HEADERS",
    "MAX_HEADER_KEY_BYTES",
    "MAX_HEADER_VALUE_BYTES",
    "Frame",
    "FrameDecodeError",
    "Header",
    "decode_frame",
    "encode_frame",
    "header_values",
    "validate_frame",
]
