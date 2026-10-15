"""Shared handlers for an instance Context and the objects it returns."""

from __future__ import annotations

from contextlib import AsyncExitStack
lazy import asyncio
lazy import inspect
lazy from typing import Any

from bazaar_core import Context

from .wire import Failure, Reference, Request, Result, decode, encode


class Session:
    """One caller owns its object table and remotely entered resources."""

    def __init__(self, ctx: Context) -> None:
        self.objects: dict[int, Any] = {0: ctx}
        self._identities = {id(ctx): 0}
        self._next = 1
        self._entered: dict[int, AsyncExitStack] = {}

    async def __aenter__(self):
        return self.dispatch

    async def __aexit__(self, *_: object) -> None:
        try:
            async with AsyncExitStack() as cleanup:
                for stack in self._entered.values():
                    cleanup.push_async_callback(stack.aclose)
        finally:
            self._entered.clear()
            self.objects.clear()
            self._identities.clear()

    def reference(self, value: object) -> Reference:
        identity = id(value)
        if identity not in self._identities:
            self._identities[identity] = self._next
            self.objects[self._next] = value
            self._next += 1
        return Reference(id=self._identities[identity])

    async def dispatch(self, request: Request) -> Result | Failure:
        try:
            target = self.objects[request.target]
            value = None
            match request.operation:
                case "get":
                    value = getattr(target, request.method)
                case "call":
                    args, kwargs = await asyncio.to_thread(
                        lambda: (
                            tuple(
                                decode(item, lambda ref: self.objects[ref.id])
                                for item in request.args
                            ),
                            {
                                key: decode(item, lambda ref: self.objects[ref.id])
                                for key, item in request.kwargs.items()
                            },
                        )
                    )
                    value = getattr(target, request.method)(*args, **kwargs)
                    if inspect.isawaitable(value):
                        value = await value
                case "enter":
                    if request.target in self._entered:
                        raise ValueError("the remote context has already been entered")
                    stack = AsyncExitStack()
                    value = await stack.enter_async_context(target)
                    self._entered[request.target] = stack
                case "exit":
                    await self._entered.pop(request.target).aclose()
                case "release":
                    if request.target == 0:
                        raise ValueError(
                            "the root Context belongs to the server lifespan"
                        )
                    if request.target in self._entered:
                        await self._entered.pop(request.target).aclose()
                    del self.objects[request.target]
                    del self._identities[id(target)]
            return Result(value=encode(value, self.reference))
        except Exception as error:  # noqa: BLE001 - Keep one failed call within its own session.
            return Failure(type=type(error).__name__, message=str(error))
