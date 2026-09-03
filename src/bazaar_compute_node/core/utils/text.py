from __future__ import annotations


def format_exception(error: BaseException) -> str:
    """Say what went wrong in the exception's own words, or name it when it has none."""

    return str(error) or type(error).__name__


def compact(count: int) -> str:
    """Write a count the way a reader skims it: 1.2K rather than 1234."""

    if abs(count) < 1000:
        return str(count)
    scaled = float(count)
    suffix = ""
    for candidate in ("K", "M", "B", "T"):
        scaled /= 1000
        suffix = candidate
        # a value that would round up to a thousand belongs to the next suffix
        if abs(scaled) < 999.95:
            break
    rounded = f"{scaled:.1f}" if abs(scaled) < 100 else f"{scaled:.0f}"
    return rounded.removesuffix(".0") + suffix


def truncate_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    suffix = "…"
    prefix = encoded[: limit - len(suffix.encode("utf-8"))]
    return prefix.decode("utf-8", errors="ignore") + suffix


__all__ = ["compact", "format_exception", "truncate_utf8"]
