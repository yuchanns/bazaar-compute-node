from __future__ import annotations

from collections.abc import Mapping

SENSITIVE_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "body",
        "cookie",
        "credential",
        "payload",
        "permission_denials",
        "raw_payload",
        "secret",
        "token",
    }
)


def is_sensitive_field(name: str) -> bool:
    return name.casefold() in SENSITIVE_FIELD_NAMES


def omit_sensitive_fields(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: omit_sensitive_fields(item)
            for key, item in value.items()
            if not isinstance(key, str) or not is_sensitive_field(key)
        }
    if isinstance(value, list):
        return [omit_sensitive_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(omit_sensitive_fields(item) for item in value)
    return value


__all__ = [
    "SENSITIVE_FIELD_NAMES",
    "is_sensitive_field",
    "omit_sensitive_fields",
]
