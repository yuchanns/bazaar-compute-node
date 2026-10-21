"""Shared handlers for an instance Context and the objects it returns."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
lazy import asyncio
lazy import inspect
lazy from typing import Any, cast

from bazaar_core import Composition, Context, Contrib, ContribEntry, ServiceKey

from .wire import Failure, Reference, Request, Result, decode, encode


async def _close(stack: AsyncExitStack) -> None:
    """Finish owned cleanup before propagating caller cancellation."""
    task = asyncio.create_task(stack.aclose())
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except Exception:  # noqa: BLE001 - Retrieve the cleanup error from the completed task.
            break
    if cancellation is not None:
        if not task.cancelled() and (error := task.exception()) is not None:
            cancellation.add_note(f"cleanup failed: {error!r}")
        raise cancellation
    task.result()


class _Caller:
    """Forward framework operations with connection-owned service consumers."""

    def __init__(self, ctx: Context, resources: AsyncExitStack) -> None:
        self._ctx = ctx
        self._resources = resources

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)

    async def accept[T](self, key: ServiceKey[T]) -> T:
        service: T | None = None

        @asynccontextmanager
        async def lifespan(ctx: Context):
            nonlocal service
            service = ctx.accept(key)
            yield

        await self._resources.enter_async_context(
            self._ctx.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="sidecar-caller",
                            definition=Contrib(accepts=[key], lifespan=lifespan),
                        )
                    ]
                )
            )
        )
        return cast(T, service)


class Session:
    """One caller owns its object table and remotely entered resources."""

    def __init__(self, ctx: Context) -> None:
        self._resources = AsyncExitStack()
        caller = _Caller(ctx, self._resources)
        self.objects: dict[int, Any] = {0: caller}
        self._identities = {id(caller): 0}
        self._next = 1
        self._entered: dict[int, AsyncExitStack] = {}
        self._locks: dict[int, asyncio.Lock] = {0: asyncio.Lock()}

    async def __aenter__(self):
        return self.dispatch

    async def __aexit__(self, *_: object) -> None:
        try:
            async with AsyncExitStack() as cleanup:
                cleanup.push_async_callback(_close, self._resources)
                for stack in self._entered.values():
                    cleanup.push_async_callback(_close, stack)
        finally:
            self._entered.clear()
            self._locks.clear()
            self.objects.clear()
            self._identities.clear()

    def reference(self, value: object) -> Reference:
        identity = id(value)
        if identity not in self._identities:
            self._identities[identity] = self._next
            self.objects[self._next] = value
            self._locks[self._next] = asyncio.Lock()
            self._next += 1
        return Reference(id=self._identities[identity])

    async def dispatch(self, request: Request) -> Result | Failure:
        try:
            value = None
            match request.operation:
                case "get":
                    value = getattr(self.objects[request.target], request.method)
                case "call":
                    target = self.objects[request.target]
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
                case "enter" | "exit" | "release":
                    async with self._locks[request.target]:
                        target = self.objects[request.target]
                        match request.operation:
                            case "enter":
                                if request.target in self._entered:
                                    raise ValueError(
                                        "the remote context has already been entered"
                                    )
                                stack = AsyncExitStack()
                                value = await stack.enter_async_context(target)
                                self._entered[request.target] = stack
                            case "exit":
                                stack = self._entered[request.target]
                                try:
                                    await _close(stack)
                                finally:
                                    del self._entered[request.target]
                            case "release":
                                if request.target == 0:
                                    raise ValueError(
                                        "the root Context belongs to the server lifespan"
                                    )
                                if request.target in self._entered:
                                    stack = self._entered[request.target]
                                    try:
                                        await _close(stack)
                                    finally:
                                        del self._entered[request.target]
                                del self.objects[request.target]
                                del self._identities[id(target)]
                                del self._locks[request.target]
            return Result(value=encode(value, self.reference))
        except Exception as error:  # noqa: BLE001 - Keep one failed call within its own session.
            return Failure(type=type(error).__name__, message=str(error))
