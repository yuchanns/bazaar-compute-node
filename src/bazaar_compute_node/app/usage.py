from __future__ import annotations

from typing import NoReturn, Protocol


class Usage(Protocol):
    """What a command runner needs to refuse a request it cannot serve."""

    def error(self, message: str) -> NoReturn: ...


__all__ = ["Usage"]
