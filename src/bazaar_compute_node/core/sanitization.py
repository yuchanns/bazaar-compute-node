from __future__ import annotations

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


def is_sensitive_field(name: str) -> bool:
    return name.casefold() in SENSITIVE_FIELD_NAMES


__all__ = [
    "SENSITIVE_FIELD_NAMES",
    "is_sensitive_field",
]
