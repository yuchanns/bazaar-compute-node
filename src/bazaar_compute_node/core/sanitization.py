from __future__ import annotations

import re
from collections.abc import Mapping

REDACTED = "<redacted>"
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "body",
        "cookie",
        "credential",
        "payload",
        "raw_payload",
        "secret",
        "token",
    }
)
_TOKEN_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+|xoxb-[A-Za-z0-9-]+|"
    r"(?<![A-Za-z0-9+/=_-])[A-Fa-f0-9]{32,}(?![A-Za-z0-9+/=_-])|"
    r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{32,}(?![A-Za-z0-9+/=_-]))"
)


def is_sensitive_field(name: str) -> bool:
    return name.casefold() in SENSITIVE_FIELD_NAMES


def redact_sensitive_text(value: str) -> str:
    return _TOKEN_PATTERN.sub(REDACTED, value)


def redact_sensitive_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED
                if is_sensitive_field(str(key))
                else redact_sensitive_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


__all__ = [
    "REDACTED",
    "SENSITIVE_FIELD_NAMES",
    "is_sensitive_field",
    "redact_sensitive_text",
    "redact_sensitive_value",
]
