"""Contrib instances, callbacks, service bindings, and ownership handles."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid7
lazy from collections.abc import Sequence
lazy from contextlib import AbstractAsyncContextManager
lazy from dataclasses import replace
lazy from inspect import isawaitable
lazy from typing import Any, Literal, cast
lazy from uuid import UUID

lazy from pydantic import BaseModel

from .contracts import Running, Starting
lazy from .bazaar import ContribHub
lazy from .contracts import (
    CURRENT_CONTEXT,
    BindingRef,
    Blocked,
    Candidate,
    Composition,
    Contrib,
    ContribEntry,
    ContribError,
    ContribState,
    ContribStatus,
    EventHandler,
    EventKey,
    Failed,
    ServiceKey,
    Stopped,
    Stopping,
)


@dataclass(frozen=True, slots=True)
class _Open:
    pass


@dataclass(frozen=True, slots=True)
class _Closing:
    """An owner is closing; its own close request may not have started yet."""

    cleanup: _Cleanup | None = None


@dataclass(frozen=True, slots=True)
class _Closed(_Closing):
    pass


type _OwnerState = _Open | _Closing | _Closed


class _Context:
    __slots__ = ("_accepted", "_instance")

    def __init__(self, instance: _ContribInstance) -> None:
        self._instance = instance
        self._accepted: dict[ServiceKey[Any], Any] = {}

    @property
    def id(self) -> UUID:
        return self._instance.id

    @property
    def name(self) -> str:
        return self._instance.entry.name

    @property
    def snapshots(self) -> tuple[ContribStatus, ...]:
        return self._instance.enablement._hub.snapshots

    def offer[T](self, key: ServiceKey[T], service: T) -> None:
        instance = self._instance
        if key not in instance.definition.offers:
            raise ContribError(
                f"{instance.label}: service {key.name!r} is not declared in Contrib.offers"
            )
        if (
            isinstance(instance.state, Stopping) and instance.state.phase == "stopped"
        ) or instance._ready.is_set():
            raise ContribError(
                f"{instance.label}: cannot provide service {key.name!r} "
                "after contrib startup has finished"
            )
        if key in instance._services:
            raise ContribError(
                f"{instance.label}: service {key.name!r} has already been provided"
            )
        instance._services[key] = service

    def accept[T](self, key: ServiceKey[T]) -> T:
        instance = self._instance
        if isinstance(instance.state, Stopping) and instance.state.phase == "stopped":
            raise ContribError(
                f"{instance.label}: contrib has stopped; cannot access service {key.name!r}"
            )
        if key in instance._services:
            return cast(T, instance._services[key])
        try:
            return cast(T, self._accepted[key])
        except KeyError:
            raise ContribError(
                f"{instance.label}: service {key.name!r} is unavailable; "
                "declare it in Contrib.accepts or call ctx.offer() before accessing it"
            ) from None

    async def announce[T](self, event: EventKey[T], value: T) -> None:
        instance = self._instance
        if isinstance(instance.state, Stopping) and instance.state.phase == "stopped":
            raise ContribError(
                f"{instance.label}: contrib has stopped; cannot publish event {event.name!r}"
            )
        await instance.enablement._hub._announce(instance.bindings, event, value)

    def enable(
        self,
        composition: Composition | Candidate,
        *,
        local: Sequence[ServiceKey[Any] | EventKey[Any]] = (),
    ) -> AbstractAsyncContextManager[Enablement]:
        if isinstance(self._instance.state, Stopping):
            raise ContribError(f"{self._instance.label}: contrib is stopping")
        return self._instance.enablement._hub._enable(
            composition, parent=self._instance, local=local
        )

    def validate(
        self,
        composition: Composition,
        *,
        local: Sequence[ServiceKey[Any] | EventKey[Any]] = (),
        replacing: Candidate | None = None,
    ) -> Candidate:
        if isinstance(self._instance.state, Stopping):
            raise ContribError(f"{self._instance.label}: contrib is stopping")
        return self._instance.enablement._hub._validate(
            composition, parent=self._instance, local=local, replacing=replacing
        )

    async def update(self, composition: Composition) -> None:
        """Replace the composition this instance supplied to ContribHub.run()."""
        if isinstance(self._instance.state, Stopping):
            raise ContribError(
                f"{self._instance.label}: cannot update its composition while stopping"
            )
        owner = self._instance._composition
        if owner is None:
            raise ContribError(
                f"{self._instance.label}: no composition from this contrib has been started by ContribHub.run()"
            )
        await self._instance.enablement._hub._update(owner, composition)


@dataclass(eq=False, slots=True)
class _ContribInstance:
    """One configured lifespan, its context, and its owned service registrations."""

    registration: _Registration
    config: BaseModel | None
    accepts: tuple[ServiceKey[Any], ...]
    id: UUID = field(default_factory=uuid7)
    context: _Context = field(init=False)
    bindings: dict[ServiceKey[Any] | EventKey[Any], UUID | None] = field(init=False)
    _order: int = 0
    _dependencies: dict[ServiceKey[Any], _ContribInstance] = field(default_factory=dict)
    _parents: tuple[_ContribInstance, ...] = ()
    _services: dict[ServiceKey[Any], Any] = field(default_factory=dict)
    _ready: asyncio.Event = field(default_factory=asyncio.Event)
    _waiting: set[_ContribInstance] = field(default_factory=set)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _callbacks: set[_Invocation] = field(default_factory=set)
    _task: asyncio.Task[None] | None = None
    state: ContribState[BaseException] = field(default_factory=Starting)
    _composition: Enablement | None = None

    def __post_init__(self) -> None:
        self.context = _Context(self)
        self.bindings = dict(self.enablement._bindings)
        self.bindings.update((key, self.registration.id) for key in self.entry.local)
        parents = []
        parent = self.enablement._parent
        while parent is not None:
            parents.append(parent)
            parent = parent.enablement._parent
        self._parents = tuple(parents)

    @property
    def entry(self) -> ContribEntry:
        return self.registration.entry

    @property
    def enablement(self) -> Enablement:
        return self.registration.owner

    @property
    def definition(self) -> Contrib[Any]:
        return self.entry.definition

    @property
    def label(self) -> str:
        return f"contrib {self.entry.name!r} ({self.id}) at {self.entry.location}"

    @property
    def status(self) -> ContribStatus:
        state = self.state
        if isinstance(state, Stopping):
            state = cast(
                ContribState[str],
                replace(
                    state,
                    error=f"{type(state.error).__name__}: {state.error}"
                    if state.error is not None
                    else None,
                    exit_error=f"{type(state.exit_error).__name__}: {state.exit_error}"
                    if state.exit_error is not None
                    else None,
                ),
            )
        return ContribStatus(
            self.id,
            self.entry.name,
            self.entry.location,
            state,
            self.enablement._parent.id if self.enablement._parent is not None else None,
        )

    async def _wait_ready(self) -> None:
        await self._ready.wait()
        state = self.state
        if isinstance(state, Stopping):
            if state.error is not None:
                raise state.error
            if state.phase != "exiting":
                raise ContribError(
                    f"{self.label}: contrib is stopping and cannot supply its services"
                )

    def _transition(
        self, phase: Literal["stopping", "exiting", "aborting", "stopped"]
    ) -> None:
        state = self.state
        if isinstance(state, (Failed, Blocked)):
            self.state = replace(state, phase=phase)
        elif phase == "stopped":
            self.state = (
                Stopped(error=state.error, exit_error=state.exit_error)
                if isinstance(state, Stopping)
                else Stopped()
            )
        else:
            self.state = (
                replace(state, phase=phase)
                if isinstance(state, Stopping)
                else Stopping(phase=phase)
            )

    def _record_failure(
        self, error: BaseException, blocked_by: UUID | None = None
    ) -> None:
        state = self.state
        phase = state.phase if isinstance(state, Stopping) else "stopping"
        exit_error = state.exit_error if isinstance(state, Stopping) else None
        self.state = (
            Blocked(
                error=error, blocked_by=blocked_by, phase=phase, exit_error=exit_error
            )
            if blocked_by is not None
            else Failed(error=error, phase=phase, exit_error=exit_error)
        )

    def _cancel(self) -> None:
        if (
            isinstance(self.state, Stopping)
            and self.state.phase == "stopping"
            and self._task is not None
        ):
            self._transition("aborting")
            self._task.cancel()

    async def _invoke(
        self, invocation: _Invocation, handler: EventHandler[Any], value: Any
    ) -> None:
        invocation.task = cast(asyncio.Task[None], asyncio.current_task())
        failure: BaseException | None = None
        token = CURRENT_CONTEXT.set(self.context)
        try:
            result = handler(self.context, value)
            if isawaitable(result):
                await result
        except asyncio.CancelledError as error:
            if not isinstance(invocation._state, _Cancelled):
                failure = error
            raise
        except BaseException as error:  # noqa: BLE001 - Announce aggregates listener failures.
            failure = error
        finally:
            CURRENT_CONTEXT.reset(token)
            invocation._state = _Finished(failure)
            self._callbacks.discard(invocation)
            invocation.done.set()


@dataclass(frozen=True, slots=True)
class _Cancelled:
    pass


@dataclass(frozen=True, slots=True)
class _Finished:
    error: BaseException | None = None


@dataclass(eq=False, slots=True)
class _Invocation:
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] = field(init=False)
    _state: Running | _Cancelled | _Finished = field(default_factory=Running)

    def cancel(self) -> None:
        if isinstance(self._state, Running):
            self._state = _Cancelled()
            self.task.cancel()


@dataclass(slots=True)
class _Cleanup:
    instances: tuple[_ContribInstance, ...]
    owners: tuple[Enablement, ...] = ()
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    _state: Running | _Finished = field(default_factory=Running)


@dataclass(frozen=True, slots=True)
class _Registration:
    entry: ContribEntry
    config: BaseModel | None
    owner: Enablement
    id: UUID = field(default_factory=uuid7)


class Enablement:
    """A handle to a composition's instances, bindings, updates, and cleanup."""

    def __init__(
        self,
        hub: ContribHub,
        parent: _ContribInstance | None = None,
        local: Sequence[ServiceKey[Any] | EventKey[Any]] = (),
    ) -> None:
        self._hub = hub
        self._parent = parent
        self._bindings = dict(parent.bindings) if parent is not None else {}
        self._bindings.update((key, uuid7()) for key in local)
        self.id = uuid7()
        self._registrations: dict[UUID, _Registration] = {}
        self._diagnostics: tuple[UUID, ...] = ()
        self._state: _OwnerState = _Open()

    def ref[T](self, key: ServiceKey[T] | EventKey[T]) -> BindingRef[T]:
        """Select this composition's service or event binding during assembly."""
        return BindingRef(key, self)

    @property
    def snapshots(self) -> tuple[ContribStatus, ...]:
        return (
            *(
                self._hub._diagnostics[id]
                for id in self._diagnostics
                if id in self._hub._diagnostics
            ),
            *(
                instance.status
                for instance in self._hub._all_instances()
                if instance.enablement is self
            ),
        )

    async def update(self, composition: Composition) -> None:
        """Replace this handle's entries while keeping its local bindings."""
        await self._hub._update(self, composition)

    async def aclose(self) -> None:
        current = asyncio.current_task()
        context = CURRENT_CONTEXT.get()
        caller = next(
            (
                instance
                for instance in self._hub._all_instances()
                if instance._task is current or instance.context is context
            ),
            None,
        )
        if caller is not None:
            owner = caller.enablement
            while owner is not self and owner._parent is not None:
                owner = owner._parent.enablement
            if owner is self:
                raise ContribError(
                    f"{caller.label}: cannot close a composition containing this contrib; "
                    "request shutdown from outside the composition"
                )
        cleanup = self._state.cleanup if isinstance(self._state, _Closing) else None
        if cleanup is None:
            async with self._hub._lock:
                cleanup = (
                    self._state.cleanup if isinstance(self._state, _Closing) else None
                )
                if cleanup is None:
                    cleanup = self._hub._release(
                        tuple(self._registrations.values()), owners=(self,)
                    )
                    self._state = _Closing(cleanup)
        await self._hub._finish_cleanup(self, cleanup)
        removed = tuple(id for id in self._diagnostics if id in self._hub._diagnostics)
        for id in removed:
            del self._hub._diagnostics[id]
        if removed:
            self._hub._notify(removed=removed)
