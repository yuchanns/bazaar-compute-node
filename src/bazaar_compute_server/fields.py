"""Reading a reported payload: a value is what it claims to be, or nothing."""

from __future__ import annotations


def text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["integer", "text"]
