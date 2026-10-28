"""Installed remote entries and the sessions that call their declared members."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
lazy import inspect
lazy from importlib.metadata import entry_points

from bazaar_core import Composition, Context, Contrib, ContribEntry, ServiceKey
from pydantic import validate_call
from pydantic_core import to_jsonable_python

from .wire import Failure, Request, Result


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    """A service and the members its package exposes to other processes."""

    service: ServiceKey[Any]
    members: tuple[str, ...]


def remotes(group: str = "bazaar_compute_node.remotes") -> dict[str, RemoteEntry]:
    entries: dict[str, RemoteEntry] = {}
    for point in entry_points(group=group):
        if point.name in entries:
            raise ValueError(f"remote entry {point.name!r} is declared more than once")
        entries[point.name] = point.load()
    return entries


class Session:
    """One connection calls the declared members of installed remote entries."""

    def __init__(self, ctx: Context, entries: dict[str, RemoteEntry]) -> None:
        self.ctx = ctx
        self.entries = entries

    async def __aenter__(self):
        return self.dispatch

    async def __aexit__(self, *_: object) -> None:
        pass

    async def dispatch(self, request: Request) -> Result | Failure:
        try:
            entry = self.entries.get(request.service)
            if entry is None or request.method not in entry.members:
                raise LookupError(
                    f"remote member is not exposed: {request.service}.{request.method}"
                )
            services: list[object] = []

            @asynccontextmanager
            async def lifespan(ctx: Context):
                services.append(ctx.accept(entry.service))
                yield

            async with self.ctx.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="sidecar-caller",
                            definition=Contrib(
                                accepts=[entry.service], lifespan=lifespan
                            ),
                        )
                    ]
                )
            ):
                value = getattr(services[0], request.method)
                if callable(value):
                    value = validate_call(value)(*request.args, **request.kwargs)
                    if inspect.isawaitable(value):
                        value = await value
            return Result(value=to_jsonable_python(value))
        except Exception as error:  # noqa: BLE001 - Keep one failed call within its own session.
            return Failure(type=type(error).__name__, message=str(error))
