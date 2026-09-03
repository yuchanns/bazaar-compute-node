from __future__ import annotations


def format_exception(error: BaseException) -> str:
    """Say what went wrong in the exception's own words, or name it when it has none."""

    return str(error) or type(error).__name__


def truncate_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    suffix = "…"
    prefix = encoded[: limit - len(suffix.encode("utf-8"))]
    return prefix.decode("utf-8", errors="ignore") + suffix


__all__ = ["format_exception", "truncate_utf8"]
