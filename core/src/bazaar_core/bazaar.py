"""Contrib hub startup, event dispatch, fault propagation, and resource cleanup."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
lazy import asyncio
lazy from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Sequence,
)
lazy from contextlib import AbstractAsyncContextManager, AsyncExitStack
lazy from dataclasses import replace
lazy from types import TracebackType
lazy from typing import Any, Self, cast
lazy from uuid import UUID

lazy from .assembly import _LOCAL, diff, sort, target
lazy from .contracts import (
    COMPOSITION,
    CONTRIB_CHANGED,
    CURRENT_CONTEXT,
    Blocked,
    Candidate,
    Composition,
    ContribChange,
    ContribEntry,
    ContribError,
    ContribStatus,
    EventKey,
    Failed,
    Running,
    ServiceKey,
    Starting,
    Stopping,
)
lazy from .instances import (
    Enablement,
    _Cleanup,
    _Closed,
    _Closing,
    _ContribInstance,
    _Finished,
    _Invocation,
    _Open,
    _OwnerState,
    _Registration,
)
lazy from .lifecycle import TaskFailureSource

logger = logging.getLogger(__name__)


class ContribHub:
    """Start and stop contrib instances, bind services, and deliver events."""

    def __init__(self) -> None:
        self._owners: dict[UUID, Enablement] = {}
        self._registrations: dict[UUID, _Registration] = {}
        self._instances: dict[UUID, _ContribInstance] = {}
        self._failed: dict[UUID, _ContribInstance] = {}
        self._diagnostics: dict[UUID, ContribStatus] = {}
        self._listeners: dict[
            tuple[EventKey[Any], UUID | None],
            dict[UUID, tuple[_ContribInstance, Callable[..., Any]]],
        ] = {}
        self._dependents: dict[_ContribInstance, dict[UUID, _ContribInstance]] = {}
        self._revision = 0
        self._order = 0
        self._state: _OwnerState = _Open()
        self._tasks: asyncio.TaskGroup | None = None
        self._lock = asyncio.Lock()
        self._update_lock = asyncio.Lock()
        self._updates: set[_Invocation] = set()

    async def __aenter__(self) -> Self:
        if self._tasks is not None or not isinstance(self._state, _Open):
            raise ContribError("hub: already entered or closed")
        self._tasks = asyncio.TaskGroup()
        await self._tasks.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        group = self._tasks
        if group is None:
            return None
        try:
            if exc is None:
                await self.aclose()
            else:
                await self._exit_after_error(self.aclose, exc)
        finally:
            self._tasks = None
            await group.__aexit__(exc_type, exc, traceback)
        return None

    @property
    def snapshots(self) -> tuple[ContribStatus, ...]:
        """Identity and lifecycle records for enabled and retained failed instances."""
        return (
            *self._diagnostics.values(),
            *(instance.status for instance in self._all_instances()),
        )

    def _all_instances(self) -> tuple[_ContribInstance, ...]:
        return (*self._instances.values(), *self._failed.values())

    def validate(
        self,
        composition: Composition,
        *,
        local: Sequence[ServiceKey[Any] | EventKey[Any]] = (),
        replacing: Candidate | None = None,
    ) -> Candidate:
        return self._validate(composition, local=local, replacing=replacing)

    def _validate(
        self,
        composition: Composition,
        *,
        parent: _ContribInstance | None = None,
        local: Sequence[ServiceKey[Any] | EventKey[Any]] = (),
        replacing: Candidate | None = None,
    ) -> Candidate:
        local = _LOCAL.validate_python(local)
        if replacing is not None:
            if replacing._hub is not self:
                raise ContribError("the composition belongs to another hub")
            owner = replacing._enablement
            if parent is not None and owner._parent is not parent:
                raise ContribError("the composition belongs to another contrib")
            if local:
                raise ContribError("a replacement retains its existing local bindings")
        else:
            owner = Enablement(self, parent, local)
        return target(self, owner, composition)

    def _register(self, candidate: Candidate) -> None:
        owner = candidate._enablement
        self._owners[owner.id] = owner
        owner._diagnostics = tuple(record.id for record in candidate._diagnostics)
        self._diagnostics.update(
            (record.id, record) for record in candidate._diagnostics
        )
        if candidate._diagnostics and self._tasks is not None:
            self._tasks.create_task(
                self._announce_change(ContribChange(candidate._diagnostics))
            )
        for registration in candidate._registrations:
            self._owners[registration.owner.id] = registration.owner
            self._registrations[registration.id] = registration
            registration.owner._registrations[registration.id] = registration
        for instance in candidate._instances:
            self._order += 1
            instance._order = self._order
            self._instances[instance.id] = instance
            for provider in (*instance._dependencies.values(), *instance._parents):
                self._dependents.setdefault(provider, {})[instance.id] = instance
            for event, handler in instance.definition.listen.items():
                self._listeners.setdefault((event, instance.bindings.get(event)), {})[
                    instance.id
                ] = (instance, handler)
        self._notify(candidate._instances)

    def _unregister(self, instance: _ContribInstance) -> None:
        self._instances.pop(instance.id, None)
        self._dependents.pop(instance, None)
        for provider in (*instance._dependencies.values(), *instance._parents):
            dependents = self._dependents.get(provider, {})
            dependents.pop(instance.id, None)
            if not dependents:
                self._dependents.pop(provider, None)
        for event in instance.definition.listen:
            binding = event, instance.bindings.get(event)
            listeners = self._listeners.get(binding, {})
            listeners.pop(instance.id, None)
            if not listeners:
                self._listeners.pop(binding, None)

    def enable(
        self,
        composition: Composition | Candidate,
        *,
        local: Sequence[ServiceKey[Any] | EventKey[Any]] = (),
    ) -> AbstractAsyncContextManager[Enablement]:
        return self._enable(composition, local=local)

    @asynccontextmanager
    async def _enable(
        self,
        composition: Composition | Candidate,
        *,
        parent: _ContribInstance | None = None,
        local: Sequence[ServiceKey[Any] | EventKey[Any]] = (),
    ) -> AsyncIterator[Enablement]:
        if self._tasks is None:
            raise ContribError("enter the hub before enabling contribs")
        local = _LOCAL.validate_python(local)
        async with self._lock:
            if parent is not None and isinstance(parent.state, Stopping):
                raise ContribError(f"{parent.label}: contrib is stopping")
            if isinstance(composition, Candidate):
                if local:
                    raise ContribError(
                        "specify local bindings when validating a candidate"
                    )
                candidate = composition
                if candidate._enablement._parent is not parent:
                    raise ContribError("the candidate belongs to another contrib")
            else:
                candidate = self._validate(composition, parent=parent, local=local)
            if candidate._hub is not self:
                raise ContribError("validation result belongs to another hub")
            if not isinstance(self._state, _Open) or not isinstance(
                candidate._enablement._state, _Open
            ):
                raise ContribError("the hub or composition is stopping")
            if candidate._revision != self._revision:
                raise ContribError(
                    "the hub changed since validation; validate the composition again"
                )
            owner = candidate._enablement
            task = asyncio.current_task()
            waiter = next(
                (
                    instance
                    for instance in self._all_instances()
                    if instance._task is task
                ),
                None,
            )
            candidate = await self._apply(candidate, parent, waiter=waiter)
            if waiter is not None:
                waiter._waiting.update(candidate._instances)
        try:
            try:
                for instance in candidate._instances:
                    await instance._ready.wait()
            finally:
                if waiter is not None:
                    waiter._waiting.difference_update(candidate._instances)
            yield owner
        except BaseException as error:
            await self._exit_after_error(owner.aclose, error)
            raise
        else:
            await owner.aclose()

    def _activate(self, candidate: Candidate) -> None:
        group = cast(asyncio.TaskGroup, self._tasks)
        self._revision += 1
        self._register(candidate)
        for instance in candidate._instances:
            instance._task = group.create_task(
                self._drive(instance), name=instance.label, eager_start=True
            )

    async def _update(self, owner: Enablement, composition: Composition) -> None:
        """Run the update in the hub's task group and return errors to its caller."""
        composition = Composition.model_validate(composition)
        group = self._tasks
        if group is None or not isinstance(self._state, _Open):
            raise ContribError("hub: the hub is not running")
        requester = next(
            (
                instance
                for instance in self._all_instances()
                if instance.context is CURRENT_CONTEXT.get()
            ),
            None,
        )
        if requester is not None and not isinstance(requester.state, Running):
            raise ContribError(
                f"{requester.label}: updates require this contrib to be in the Running state"
            )
        invocation = _Invocation()
        self._updates.add(invocation)
        invocation.task = group.create_task(
            self._apply_update(owner, composition, requester, invocation),
            name="contrib composition update",
            eager_start=True,
        )
        if requester is None:
            await asyncio.shield(invocation.done.wait())
        else:
            async with asyncio.TaskGroup() as waiting:
                done = waiting.create_task(invocation.done.wait())
                stopping = waiting.create_task(requester._stop.wait())
                await asyncio.wait(
                    (done, stopping), return_when=asyncio.FIRST_COMPLETED
                )
                if not invocation.done.is_set():
                    invocation.cancel()
                stopping.cancel()
                await done
        state = invocation._state
        if isinstance(state, _Finished) and state.error is not None:
            raise state.error

    async def _apply_update(
        self,
        owner: Enablement,
        composition: Composition,
        requester: _ContribInstance | None,
        invocation: _Invocation,
    ) -> None:
        try:
            async with self._update_lock:
                async with self._lock:
                    if not isinstance(self._state, _Open) or not isinstance(
                        owner._state, _Open
                    ):
                        raise ContribError("the composition is stopping or has closed")
                    candidate = await self._apply(
                        target(self, owner, composition), requester
                    )
                for instance in candidate._instances:
                    await instance._ready.wait()
                errors: list[BaseException] = []
                for instance in candidate._instances:
                    state = instance.state
                    if (
                        isinstance(state, Stopping)
                        and state.error is not None
                        and all(state.error is not error for error in errors)
                    ):
                        errors.append(state.error)
                if len(errors) == 1:
                    raise errors[0]
                if errors:
                    raise BaseExceptionGroup("contrib update startup failed", errors)
        except BaseException as error:  # noqa: BLE001 - Return failures to the requesting operation.
            invocation._state = _Finished(error)
        else:
            invocation._state = _Finished()
        finally:
            self._updates.discard(invocation)
            invocation.done.set()

    async def _apply(
        self,
        candidate: Candidate,
        requester: _ContribInstance | None = None,
        *,
        waiter: _ContribInstance | None = None,
    ) -> Candidate:
        changes = diff(self, candidate._enablement, candidate)
        for instance in (requester, waiter):
            if instance in changes.affected:
                assert instance is not None
                raise ContribError(
                    f"{instance.label}: cannot request an update that would stop this contrib; "
                    "submit it from a task that the update will not stop"
                )
        preview = candidate
        owner = preview._enablement
        kept = dict(changes.retained)
        affected = set(changes.affected)
        actual = {
            instance: kept.get(instance.registration.id, instance)
            for instance in preview._instances
        }
        instances = set(actual.values())
        dependencies: dict[_ContribInstance, set[_ContribInstance]] = {}
        for instance, replacement in actual.items():
            parents = {actual.get(parent, parent) for parent in instance._parents}
            providers = {
                actual.get(provider, provider)
                for provider in instance._dependencies.values()
            }
            dependencies[replacement] = {
                provider
                for provider in providers
                if provider not in parents and not provider._ready.is_set()
            } | {
                child
                for child in replacement._waiting
                if child in instances and not child._ready.is_set()
            }
        if waiter is not None:
            dependencies[waiter].update(
                replacement
                for instance, replacement in actual.items()
                if instance is replacement and not replacement._ready.is_set()
            )
        sort(tuple(actual.values()), dependencies=dependencies)
        if changes.registrations or changes.closed or affected:
            cleanup = self._release(
                changes.registrations, owners=changes.closed, instances=tuple(affected)
            )
            await self._finish_cleanup(None, cleanup)
        while lost := {
            instance
            for instance in kept.values()
            if self._instances.get(instance.id, self._failed.get(instance.id))
            is not instance
        }:
            affected.update(lost)
            kept = {
                id: instance for id, instance in kept.items() if instance not in lost
            }
            await self._finish_cleanup(None, self._release((), instances=tuple(lost)))
        if not isinstance(self._state, _Open):
            raise ContribError("the hub stopped during the update")
        removed = []
        for instance in affected:
            if self._failed.pop(instance.id, None) is not None:
                removed.append(instance.id)
        restarted = {
            (instance.enablement, instance.entry.name, instance.entry.location)
            for instance in preview._instances
            if instance.registration.id not in kept
        }
        for group in self._owners.values():
            for id in group._diagnostics:
                record = self._diagnostics.get(id)
                if record is not None and (
                    group is owner or (group, record.name, record.location) in restarted
                ):
                    del self._diagnostics[id]
                    removed.append(id)
        if removed:
            self._notify(removed=removed)
        for registration in preview._registrations:
            self._registrations[registration.id] = registration
            registration.owner._registrations[registration.id] = registration
        for instance in kept.values():
            instance.registration = self._registrations[instance.registration.id]
        actual = {
            instance: kept.get(instance.registration.id, instance)
            for instance in preview._instances
        }
        for instance, replacement in actual.items():
            if instance is replacement:
                instance._dependencies = {
                    key: actual.get(provider, provider)
                    for key, provider in instance._dependencies.items()
                }
                instance._parents = tuple(
                    actual.get(parent, parent) for parent in instance._parents
                )
        preview = replace(
            preview,
            _hub=self,
            _revision=self._revision,
            _instances=sort(
                tuple(
                    instance
                    for instance, replacement in actual.items()
                    if instance is replacement
                )
            ),
        )
        self._activate(preview)
        self._notify(tuple(kept.values()))
        return preview

    @asynccontextmanager
    async def run(
        self,
        bootstrap: Composition,
        load: ContribEntry,
    ) -> AsyncIterator[ContribHub]:
        """Start bootstrap contribs, then the loader, then its COMPOSITION entries."""
        load = load.model_copy(
            update={
                "required_offers": (*load.required_offers, COMPOSITION),
            }
        )
        async with self, AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(self.enable(bootstrap))
                owner = await stack.enter_async_context(
                    self.enable(Composition(entries=(load,)))
                )
                instance = next(
                    instance
                    for instance in self._all_instances()
                    if instance.enablement is owner
                )
                await instance._wait_ready()
                candidate = cast(
                    Composition | Candidate, instance._services[COMPOSITION]
                )
                instance._composition = await stack.enter_async_context(
                    self._enable(candidate, parent=instance)
                )
                yield self
            except BaseException as error:
                await self._exit_after_error(self.aclose, error)
                raise
            else:
                await self.aclose()

    @staticmethod
    async def _exit_after_error(
        close: Callable[[], Awaitable[None]], error: BaseException
    ) -> None:
        try:
            await close()
        except asyncio.CancelledError as cleanup:
            cleanup.add_note(f"interrupted while unwinding: {error!r}")
            raise
        except BaseException as cleanup:  # noqa: BLE001 - The caller re-raises its preserved cancellation.
            if isinstance(error, asyncio.CancelledError):
                error.add_note(f"cleanup failed: {cleanup!r}")
            else:
                raise BaseExceptionGroup(
                    "contrib operation and cleanup failed", [error, cleanup]
                ) from None

    async def aclose(self) -> None:
        current = asyncio.current_task()
        context = CURRENT_CONTEXT.get()
        caller = next(
            (
                instance
                for instance in self._instances.values()
                if instance._task is current
                or instance.context is context
                or any(invocation.task is current for invocation in instance._callbacks)
            ),
            None,
        )
        if caller is not None:
            raise ContribError(
                f"{caller.label}: cannot close a hub containing this contrib; "
                "request shutdown from outside the hub"
            )
        if isinstance(self._state, _Open):
            self._state = _Closing()
        updates = tuple(self._updates)
        for invocation in updates:
            invocation.cancel()
        for invocation in updates:
            await asyncio.shield(invocation.done.wait())
        cleanup = self._state.cleanup if isinstance(self._state, _Closing) else None
        if cleanup is None:
            async with self._lock:
                cleanup = (
                    self._state.cleanup if isinstance(self._state, _Closing) else None
                )
                if cleanup is None:
                    cleanup = self._release(
                        tuple(self._registrations.values()),
                        owners=tuple(self._owners.values()),
                    )
                    self._state = _Closing(cleanup)
        await self._finish_cleanup(self, cleanup)

    async def _observe(
        self, instance: _ContribInstance, source: TaskFailureSource
    ) -> None:
        try:
            await source.wait_failure()
            raise RuntimeError(
                f"{instance.label}: resource wait_failure() returned without reporting an exception"
            )
        except BaseException as error:  # noqa: BLE001 - Forward failures to the supervising hub.
            if not (
                isinstance(instance.state, Stopping)
                and instance.state.phase != "exiting"
            ):
                self._fail(instance, error)

    async def announce[T](self, event: EventKey[T], value: T) -> None:
        await self._announce({}, event, value)

    async def _announce[T](
        self,
        bindings: dict[ServiceKey[Any] | EventKey[Any], UUID | None],
        event: EventKey[T],
        value: T,
    ) -> None:
        group = self._tasks
        if group is None:
            raise ContribError("enter the hub before announcing events")
        callbacks: list[_Invocation] = []
        for instance, handler in tuple(
            self._listeners.get((event, bindings.get(event)), {}).values()
        ):
            if isinstance(instance.state, Running):
                invocation = _Invocation()
                instance._callbacks.add(invocation)
                invocation.task = group.create_task(
                    instance._invoke(invocation, handler, value),
                    name=f"{instance.entry.name}:{event.name}",
                    eager_start=True,
                )
                callbacks.append(invocation)
        if not callbacks:
            return
        pending = asyncio.gather(
            *(invocation.task for invocation in callbacks), return_exceptions=True
        )
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            for invocation in callbacks:
                invocation.cancel()
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    pass
            raise
        errors = [
            item._state.error
            for item in callbacks
            if isinstance(item._state, _Finished) and item._state.error is not None
        ]
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup(f"event {event.name!r} failed", errors)

    def _notify(
        self,
        instances: Sequence[_ContribInstance] = (),
        removed: Sequence[UUID] = (),
    ) -> None:
        group = self._tasks
        if group is not None:
            change = ContribChange(
                tuple(instance.status for instance in instances), tuple(removed)
            )
            group.create_task(
                self._announce_change(change), name="contrib state change"
            )

    async def _announce_change(self, change: ContribChange) -> None:
        try:
            if not isinstance(self._state, _Closed):
                await self.announce(CONTRIB_CHANGED, change)
        except BaseException as error:
            logger.error("Contrib state listener failed", exc_info=error)

    def _fail(self, instance: _ContribInstance, error: BaseException) -> None:
        instance._record_failure(error)
        error.add_note(instance.label)
        affected = {instance}
        pending = [instance]
        while pending:
            for item in self._dependents.get(pending.pop(), {}).values():
                if item not in affected and not (
                    isinstance(item.state, Stopping) and item.state.phase == "stopped"
                ):
                    affected.add(item)
                    pending.append(item)
        instances = tuple(sorted(affected, key=lambda item: item._order))
        current = asyncio.current_task()
        for item in instances:
            if item is not instance and not (
                isinstance(item.state, Stopping) and item.state.error is not None
            ):
                item._record_failure(error, instance.id)
            if not (isinstance(item.state, Stopping) and item.state.phase != "exiting"):
                item._transition("stopping")
            for invocation in tuple(item._callbacks):
                invocation.cancel()
            if (
                not item._ready.is_set()
                and item._task is not None
                and item._task is not current
            ):
                item._cancel()
        self._notify(instances)
        self._revision += 1
        cleanup = _Cleanup(instances, ())
        cleanup.task = cast(asyncio.TaskGroup, self._tasks).create_task(
            self._stop(cleanup), name=f"{instance.label}: failure cleanup"
        )
        logger.error(
            "%s failed; stopping %s",
            instance.label,
            ", ".join(item.label for item in instances),
            exc_info=error,
        )

    async def _drive(self, instance: _ContribInstance) -> None:
        # Enter and exit every lifespan in the task that owns its context.
        interrupted: asyncio.CancelledError | None = None
        token = CURRENT_CONTEXT.set(instance.context)
        try:
            async with AsyncExitStack() as stack:
                for key, provider in instance._dependencies.items():
                    if provider in instance._parents and not provider._ready.is_set():
                        if key not in provider._services or isinstance(
                            provider.state, Stopping
                        ):
                            raise ContribError(
                                f"{instance.label}: cannot start because service {key.name!r} "
                                f"from {provider.label} is unavailable"
                            )
                    else:
                        await provider._wait_ready()
                instance.context._accepted = {
                    key: provider._services[key]
                    for key, provider in instance._dependencies.items()
                }
                lifespan = cast(
                    "Callable[..., AbstractAsyncContextManager[None]] | None",
                    instance.definition.lifespan,
                )
                if lifespan is not None:
                    await stack.enter_async_context(
                        lifespan(instance.context)
                        if instance.config is None
                        else lifespan(instance.context, instance.config)
                    )
                try:
                    for key in instance.definition.offers:
                        if key not in instance._services:
                            raise ContribError(
                                f"{instance.label}: contrib finished startup without "
                                f"providing its declared service {key.name!r}"
                            )
                    if isinstance(instance.state, Starting):
                        instance.state = Running()
                    instance._ready.set()
                    self._notify((instance,))
                    async with asyncio.TaskGroup() as group:
                        seen: set[int] = set()
                        watchers: list[asyncio.Task[None]] = []
                        for service in instance._services.values():
                            if (
                                isinstance(service, TaskFailureSource)
                                and id(service) not in seen
                            ):
                                seen.add(id(service))
                                watchers.append(
                                    group.create_task(
                                        self._observe(instance, service),
                                        name=f"{instance.label}: failure",
                                    )
                                )
                        try:
                            await instance._stop.wait()
                        except asyncio.CancelledError as error:
                            if not (
                                isinstance(instance.state, Stopping)
                                and instance.state.phase != "exiting"
                            ):
                                # A lifespan's TaskGroup cancels its owning task before
                                # __aexit__ reveals the original child exception.
                                interrupted = error
                                self._fail(instance, error)
                            if not (
                                isinstance(instance.state, Stopping)
                                and instance.state.phase == "aborting"
                            ):
                                await instance._stop.wait()
                            raise
                        finally:
                            if isinstance(instance.state, Running):
                                instance._transition("exiting")
                            for task in watchers:
                                task.cancel()
                            for invocation in tuple(instance._callbacks):
                                await invocation.done.wait()
                finally:
                    if isinstance(instance.state, Running):
                        instance._transition("exiting")
        except BaseException as error:
            state = instance.state
            failure = state.error if isinstance(state, Stopping) else None
            if interrupted is not None:
                instance._record_failure(error)
                error.add_note(instance.label)
                affected = [instance]
                for dependent in self._all_instances():
                    state = dependent.state
                    if isinstance(state, Blocked) and state.blocked_by == instance.id:
                        dependent._record_failure(error, state.blocked_by)
                        affected.append(dependent)
                self._notify(affected)
                logger.error("%s failed", instance.label, exc_info=error)
            state = instance.state
            if isinstance(state, Stopping) and state.phase != "exiting":
                if failure is None:
                    state = replace(state, error=error)
                if (
                    interrupted is None
                    and not isinstance(error, asyncio.CancelledError)
                    and error is not failure
                ):
                    state = replace(state, exit_error=error)
                instance.state = state
            else:
                self._fail(instance, error)
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            instance._transition("stopped")
            instance.context._accepted.clear()
            instance._services.clear()
            instance._ready.set()
            CURRENT_CONTEXT.reset(token)

    def _release(
        self,
        registrations: Sequence[_Registration],
        *,
        owners: Sequence[Enablement] = (),
        instances: Sequence[_ContribInstance] = (),
    ) -> _Cleanup:
        records = {record.id: record for record in registrations}
        selected = set(owners)
        affected = set(instances) | {
            instance
            for instance in self._instances.values()
            if instance.registration.id in records
        }
        while True:
            children = {
                group
                for group in self._owners.values()
                if group not in selected
                and group._parent is not None
                and (group._parent in affected or group._parent.enablement in selected)
            }
            if not children:
                break
            selected.update(children)
            records.update(
                (record.id, record)
                for group in children
                for record in group._registrations.values()
            )
            affected.update(
                instance
                for instance in self._instances.values()
                if instance.registration.id in records
            )
        pending = list(affected)
        while pending:
            provider = pending.pop()
            for instance in self._dependents.get(provider, {}).values():
                if (
                    instance in affected
                    or isinstance(instance.state, Stopping)
                    and instance.state.phase == "stopped"
                ):
                    continue
                if (
                    not isinstance(instance.state, Stopping)
                    or instance.state.phase == "exiting"
                ):
                    raise ContribError(
                        f"{instance.label}: still depends on {provider.label}; "
                        "stop this contrib before stopping that dependency"
                    )
                affected.add(instance)
                pending.append(instance)
        instances = tuple(sorted(affected, key=lambda instance: instance._order))
        current = asyncio.current_task()
        if any(
            invocation.task is current
            for instance in instances
            for invocation in instance._callbacks
        ):
            raise ContribError("request shutdown outside the affected event listener")
        removed = []
        for group in selected:
            for id in group._diagnostics:
                if self._diagnostics.pop(id, None) is not None:
                    removed.append(id)
        for instance in tuple(self._failed.values()):
            if instance.registration.id in records or instance.enablement in selected:
                self._failed.pop(instance.id, None)
                removed.append(instance.id)
        for registration in records.values():
            self._registrations.pop(registration.id, None)
            registration.owner._registrations.pop(registration.id, None)
        cleanup = _Cleanup(instances, tuple(selected))
        for group in selected:
            group._state = _Closing(cleanup)
        for instance in instances:
            if (
                not isinstance(instance.state, Stopping)
                or instance.state.phase == "exiting"
            ):
                instance._transition("stopping")
            if not instance._ready.is_set():
                instance._cancel()
        self._notify(instances, removed)
        self._revision += 1
        if self._tasks is None:
            for instance in instances:
                instance._transition("stopped")
                instance._ready.set()
                self._unregister(instance)
            for group in selected:
                group._state = _Closed(cleanup)
                self._owners.pop(group.id, None)
            cleanup._state = _Finished()
        else:
            cleanup.task = self._tasks.create_task(
                self._stop(cleanup), name="contrib cleanup"
            )
        return cleanup

    async def _abort(self, instance: _ContribInstance, cleanup: _Cleanup) -> None:
        await cleanup.abort.wait()
        for invocation in tuple(instance._callbacks):
            await invocation.done.wait()
        instance._cancel()

    async def _stop(self, cleanup: _Cleanup) -> None:
        failure: BaseException | None = None
        try:
            errors: list[BaseException] = []
            for instance in reversed(sort(cleanup.instances)):
                instance._stop.set()
                if instance._task is not None:
                    async with asyncio.TaskGroup() as group:
                        abort = group.create_task(
                            self._abort(instance, cleanup),
                            name="contrib cleanup cancellation",
                        )
                        try:
                            await asyncio.shield(instance._task)
                        except asyncio.CancelledError:
                            if not instance._task.cancelled():
                                raise
                        finally:
                            abort.cancel()
                state = instance.state
                if isinstance(state, Stopping) and state.exit_error is not None:
                    errors.append(state.exit_error)
                instance._transition("stopped")
                instance._services.clear()
                instance._ready.set()
                self._unregister(instance)
                if (
                    isinstance(instance.state, (Failed, Blocked))
                    and isinstance(instance.enablement._state, _Open)
                    and instance.registration.id in self._registrations
                ):
                    self._failed[instance.id] = instance
                    self._notify((instance,))
                else:
                    self._notify((instance,), (instance.id,))
            for owner in cleanup.owners:
                owner._state = _Closed(cleanup)
                self._owners.pop(owner.id, None)
            if errors:
                raise BaseExceptionGroup("contrib cleanup failed", errors)
        except BaseException as error:
            failure = error
            logger.error(
                "Cleanup failed for %s",
                ", ".join(instance.label for instance in cleanup.instances),
                exc_info=error,
            )
        finally:
            cleanup._state = _Finished(failure)

    async def _finish_cleanup(
        self, owner: Enablement | ContribHub | None, cleanup: _Cleanup
    ) -> None:
        if (
            owner is not None
            and isinstance(owner._state, _Closed)
            and isinstance(cleanup._state, _Finished)
        ):
            return
        if isinstance(owner, Enablement) and any(
            instance._task is asyncio.current_task()
            or instance.context is CURRENT_CONTEXT.get()
            for instance in cleanup.instances
        ):
            tasks = tuple(
                instance._task
                for instance in cleanup.instances
                if instance.enablement is owner and instance._task is not None
            )
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            owner._state = _Closed(cleanup)
            return
        try:
            task = cleanup.task
            cancellation: asyncio.CancelledError | None = None
            while task is not None and not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as error:
                    if cancellation is None:
                        cancellation = error
                        for instance in cleanup.instances:
                            for invocation in tuple(instance._callbacks):
                                invocation.cancel()
                        cleanup.abort.set()
            state = cleanup._state
            error = state.error if isinstance(state, _Finished) else None
            if cancellation is not None:
                if error is not None:
                    cancellation.add_note(f"cleanup failed: {error!r}")
                raise cancellation
            if error is not None:
                raise error
        finally:
            if owner is not None and isinstance(cleanup._state, _Finished):
                owner._state = _Closed(cleanup)
