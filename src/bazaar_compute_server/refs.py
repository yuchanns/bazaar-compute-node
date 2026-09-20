"""Short numbers for the long values a link would otherwise carry: a uuid, a
target, whatever a page names an object by. The table behind them does not
know what a value is; the code that puts a number in a link does."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from functools import wraps
from typing import Protocol

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from .access import Handler
from .storage import IStorage

# how many digits a number can have: the table's are 64-bit
_DIGITS = 18


class Refs:
    """The numbers, kept in the process once seen: a value's number never
    changes, so what was read once holds for good."""

    def __init__(self, storage: IStorage) -> None:
        self._storage = storage
        self._by_value: dict[str, int] = {}
        self._by_ref: dict[int, str] = {}

    async def load(self, values: Iterable[str]) -> None:
        """Have a number for each of these values, before a page names them."""

        unknown = list({value for value in values if value not in self._by_value})
        if unknown:
            self._keep(zip(unknown, await self._storage.shorten(unknown), strict=True))

    def ref(self, value: str) -> int:
        """The number a loaded value goes by."""

        return self._by_value[value]

    async def expand(self, refs: Sequence[int]) -> list[str | None]:
        """The values behind numbers a link brought back; nothing for a
        number that was never given."""

        unknown = list({ref for ref in refs if ref not in self._by_ref})
        if unknown:
            found = await self._storage.expand(unknown)
            self._keep(
                (value, ref)
                for ref, value in zip(unknown, found, strict=True)
                if value is not None
            )
        return [self._by_ref.get(ref) for ref in refs]

    async def values(self, shorts: Sequence[str]) -> list[str] | None:
        """What the numbers in a link stand for, as the link spells them;
        nothing at all when one is not a number or stands for nothing."""

        # decimal digits only, and no more of them than a number can have
        if not all(short.isdecimal() and len(short) <= _DIGITS for short in shorts):
            return None
        found = await self.expand([int(short) for short in shorts])
        if None in found:
            return None
        return [value for value in found if value is not None]

    def _keep(self, pairs: Iterable[tuple[str, int]]) -> None:
        for value, ref in pairs:
            self._by_value[value] = ref
            self._by_ref[ref] = value


class Referring(Protocol):
    refs: Refs


def expanded[S: Referring](*params: str) -> Callable[[Handler[S]], Handler[S]]:
    """The handler's path parameters are numbers a page put in a link: they
    are put back to the values they stand for before the handler, or any
    check on it, sees them; a number that stands for nothing is not there."""

    def guard(handler: Handler[S]) -> Handler[S]:
        @wraps(handler)
        async def guarded(self: S, request: Request) -> Response:
            values = await self.refs.values(
                [request.path_params[param] for param in params]
            )
            if values is None:
                return HTMLResponse("", status_code=404)
            request.path_params.update(zip(params, values, strict=True))
            return await handler(self, request)

        return guarded

    return guard


__all__ = ["Referring", "Refs", "expanded"]
