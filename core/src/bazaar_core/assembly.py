"""Complete instance graphs, service bindings, and dependency ordering."""

from __future__ import annotations

from typing import Any
lazy from collections.abc import Sequence
lazy from dataclasses import dataclass, replace
lazy from heapq import heapify, heappop, heappush
lazy from uuid import UUID, uuid7

from pydantic import ConfigDict, TypeAdapter
lazy from packaging.specifiers import SpecifierSet
lazy from packaging.version import Version

from .contracts import ContribError, EventKey, ServiceKey
lazy from .bazaar import ContribHub
lazy from .contracts import (
    API_VERSION,
    Blocked,
    Candidate,
    Composition,
    ContribStatus,
    Failed,
    Stopping,
)
lazy from .instances import Enablement, _ContribInstance, _Open, _Registration

_LOCAL = TypeAdapter(
    tuple[ServiceKey[Any] | EventKey[Any], ...],
    config=ConfigDict(arbitrary_types_allowed=True),
)


def validate(
    hub: ContribHub,
    owner: Enablement,
    composition: Composition,
    registrations: Sequence[_Registration],
) -> Candidate:
    """Bind every concrete instance before acquiring any resource."""
    instances: list[_ContribInstance] = []
    diagnostics = list(composition.diagnostics)
    unavailable = {
        registration.id
        for registration in registrations
        for snapshot in registration.owner.snapshots
        if snapshot.name == registration.entry.name
        and snapshot.location == registration.entry.location
        and isinstance(snapshot.state, Stopping)
    }
    for registration in registrations:
        entry = registration.entry
        definition = entry.definition
        label = f"contrib {entry.name!r} at {entry.location}"
        try:
            if Version(API_VERSION) not in SpecifierSet(definition.api):
                raise ContribError(
                    f"{label}: requires core API {definition.api}, "
                    f"but this hub provides {API_VERSION}"
                )
            for key in entry.required_offers:
                if key not in definition.offers:
                    raise ContribError(
                        f"{label}: required service {key.name!r} is not declared in Contrib.offers"
                    )
            config = (
                registration.config.model_copy(deep=True)
                if registration.config is not None
                else None
            )
            accepts = definition.dependencies(config)
            instances.append(_ContribInstance(registration, config, accepts))
        except Exception as error:
            if (
                not composition.isolate_errors
                and composition.roots is None
                and registration.id not in unavailable
            ):
                raise ContribError(
                    f"{label}: cannot validate contrib definition: {error}"
                ) from error
            if registration.owner is owner:
                diagnostics.append(
                    ContribStatus(
                        uuid7(),
                        entry.name,
                        entry.location,
                        Failed(error=str(error), phase="stopped"),
                    )
                )
    by_registration = {instance.registration.id: instance for instance in instances}
    for instance in instances:
        instance._parents = tuple(
            by_registration.get(parent.registration.id, parent)
            for parent in instance._parents
        )
    declarations: dict[tuple[Enablement, str, str], list[_ContribInstance]] = {}
    scoped: dict[tuple[Enablement, ServiceKey[Any]], list[_ContribInstance]] = {}
    for instance in instances:
        declarations.setdefault(
            (instance.enablement, instance.entry.name, instance.entry.location), []
        ).append(instance)
        for key in instance.definition.offers:
            scoped.setdefault((instance.enablement, key), []).append(instance)
    resolved: set[tuple[_ContribInstance, ServiceKey[Any] | EventKey[Any]]] = set()
    resolving: set[tuple[_ContribInstance, ServiceKey[Any] | EventKey[Any]]] = set()

    def binding(
        instance: _ContribInstance, key: ServiceKey[Any] | EventKey[Any]
    ) -> UUID | None:
        selected = instance, key
        reference = instance.entry.bindings.get(key)
        if reference is None or selected in resolved:
            return instance.bindings.get(key)
        if selected in resolving:
            raise ContribError(
                f"{instance.label}: binding reference cycle for {key.name!r}"
            )
        resolving.add(selected)
        try:
            source = reference._source
            if isinstance(source, tuple):
                providers = declarations.get((instance.enablement, *source), [])
            else:
                if source._hub is not hub:
                    raise ContribError(
                        f"{instance.label}: binding reference belongs to another hub"
                    )
                if isinstance(key, EventKey):
                    instance.bindings[key] = source._bindings.get(key)
                    resolved.add(selected)
                    return instance.bindings[key]
                providers = scoped.get((source, key), [])
            if len(providers) != 1:
                raise ContribError(
                    f"{instance.label}: binding reference for {key.name!r} "
                    f"requires one provider; found {len(providers)}"
                )
            provider = providers[0]
            if isinstance(key, ServiceKey) and key not in provider.definition.offers:
                raise ContribError(
                    f"{instance.label}: {provider.label} does not offer {key.name!r}"
                )
            instance.bindings[key] = binding(provider, key)
            resolved.add(selected)
            return instance.bindings[key]
        finally:
            resolving.remove(selected)

    errors: dict[_ContribInstance, ContribError] = {}
    for instance in instances:
        for key in instance.entry.bindings:
            try:
                binding(instance, key)
            except ContribError as error:
                errors[instance] = error
                instance.state = Blocked(
                    error=error,
                    phase="stopped",
                    missing=(key.name,) if isinstance(key, ServiceKey) else (),
                )
    if composition.roots is not None:
        # Select concrete providers using the same keys and bindings as startup.
        available: dict[
            tuple[ServiceKey[Any], UUID | None], list[_ContribInstance]
        ] = {}
        for instance in instances:
            for key in instance.definition.offers:
                available.setdefault((key, instance.bindings.get(key)), []).append(
                    instance
                )
        roots = set(composition.roots)
        known = {entry.name for entry in composition.entries} | {
            record.name for record in composition.diagnostics
        }
        if missing := roots - known:
            raise ContribError(
                f"unknown composition roots: {', '.join(sorted(missing))}"
            )
        selected: set[_ContribInstance] = set()
        pending = [
            instance
            for instance in instances
            if instance.enablement is owner and instance.entry.name in roots
        ]
        while pending:
            instance = pending.pop()
            if instance in selected:
                continue
            selected.add(instance)
            if instance.enablement is not owner:
                continue
            pending.extend(instance._parents)
            for key in instance.accepts:
                pending.extend(available.get((key, instance.bindings.get(key)), ()))
        instances = [
            instance
            for instance in instances
            if instance.enablement is not owner or instance in selected
        ]
        identities = {instance.registration.id for instance in instances}
        registrations = tuple(
            record
            for record in registrations
            if record.owner is not owner or record.id in identities
        )
        locations = {
            (instance.entry.name, instance.entry.location) for instance in selected
        }
        diagnostics = [
            record
            for record in diagnostics
            if record.name in roots or (record.name, record.location) in locations
        ]
        if not composition.isolate_errors and diagnostics:
            raise ContribError(
                "; ".join(
                    f"contrib {record.name!r} at {record.location}: {record.state}"
                    for record in diagnostics
                )
            )
    providers: dict[tuple[ServiceKey[Any], UUID | None], _ContribInstance] = {}
    names: dict[tuple[str, UUID | None], _ContribInstance] = {}
    for instance in instances:
        if (
            instance in errors
            and not composition.isolate_errors
            and instance.registration.id not in unavailable
        ):
            raise errors[instance]
        for key in instance.definition.offers:
            identity = instance.bindings.get(key)
            previous = names.get((key.name, identity))
            if previous is not None:
                error = ContribError(
                    f"{instance.label}: service {key.name!r} is also provided by "
                    f"{previous.label} with the same binding"
                )
                if (
                    not composition.isolate_errors
                    or previous.enablement is not owner
                    or instance.enablement is not owner
                ):
                    raise error
                previous._record_failure(error)
                instance._record_failure(error)
            providers[key, identity] = instance
            names[key.name, identity] = instance
    for instance in instances:
        for key in instance.accepts:
            provider = providers.get((key, instance.bindings.get(key)))
            if provider is None:
                error = ContribError(
                    f"{instance.label}: no contrib provides service {key.name!r} with the required binding"
                )
                if (
                    not composition.isolate_errors
                    and instance.registration.id not in unavailable
                ):
                    raise error
                instance.state = Blocked(
                    error=error,
                    phase="stopped",
                    missing=tuple(
                        sorted(
                            {
                                *(
                                    instance.state.missing
                                    if isinstance(instance.state, Blocked)
                                    else ()
                                ),
                                key.name,
                            }
                        )
                    ),
                )
            else:
                instance._dependencies[key] = provider
    dependents: dict[_ContribInstance, list[_ContribInstance]] = {}
    for instance in instances:
        for provider in (*instance._dependencies.values(), *instance._parents):
            dependents.setdefault(provider, []).append(instance)
    pending = [
        instance for instance in instances if isinstance(instance.state, Stopping)
    ]
    failed = set(pending)
    while pending:
        provider = pending.pop()
        for instance in dependents.get(provider, ()):
            if instance not in failed:
                instance.state = Blocked(
                    error=ContribError(
                        f"{instance.label}: dependency failed: {provider.label}"
                    ),
                    blocked_by=provider.id,
                    phase="stopped",
                    missing=provider.state.missing
                    if isinstance(provider.state, Blocked)
                    else (),
                )
                failed.add(instance)
                pending.append(instance)
            elif isinstance(instance.state, Blocked) and isinstance(
                provider.state, Blocked
            ):
                missing = tuple(
                    sorted(set(instance.state.missing) | set(provider.state.missing))
                )
                if missing != instance.state.missing:
                    instance.state = replace(instance.state, missing=missing)
                    pending.append(instance)
    active = [instance for instance in instances if instance not in failed]
    try:
        ordered = sort(active)
    except _CycleError as error:
        if not composition.isolate_errors and any(
            instance.registration.id not in unavailable for instance in error.instances
        ):
            raise
        for instance in error.instances:
            instance.state = Blocked(error=error, phase="stopped")
        failed.update(error.instances)
        ordered = sort([instance for instance in active if instance not in failed])
    for instance in instances:
        if instance in failed and instance.enablement is owner:
            instance._transition("stopped")
            diagnostics.append(instance.status)
    return Candidate(
        ordered, hub, hub._revision, tuple(registrations), owner, tuple(diagnostics)
    )


class _CycleError(ContribError):
    def __init__(self, message: str, instances: Sequence[_ContribInstance]) -> None:
        super().__init__(message)
        self.instances = tuple(instances)


def sort(
    instances: Sequence[_ContribInstance],
    *,
    dependencies: dict[_ContribInstance, set[_ContribInstance]] | None = None,
) -> tuple[_ContribInstance, ...]:
    """Order providers and parents first, or validate explicit waiting edges."""
    pending = {
        instance: order for order, instance in enumerate(dict.fromkeys(instances))
    }
    if dependencies is None:
        dependencies = {
            instance: {
                *instance._dependencies.values(),
                *instance._parents,
            }.intersection(pending)
            for instance in pending
        }
    else:
        dependencies = {
            instance: dependencies[instance].intersection(pending)
            for instance in pending
        }
    dependents: dict[_ContribInstance, list[_ContribInstance]] = {}
    for instance, providers in dependencies.items():
        for provider in providers:
            dependents.setdefault(provider, []).append(instance)
    ready = [
        (instance._order, order, instance)
        for instance, order in pending.items()
        if not dependencies[instance]
    ]
    heapify(ready)
    result: list[_ContribInstance] = []
    while ready:
        _, _, instance = heappop(ready)
        del pending[instance]
        result.append(instance)
        for dependent in dependents.get(instance, ()):
            dependencies[dependent].remove(instance)
            if not dependencies[dependent]:
                heappush(ready, (dependent._order, pending[dependent], dependent))
    if pending:
        details = [
            f"{instance.label} waits for {provider.label}"
            for instance in pending
            for provider in dependencies[instance]
        ]
        raise _CycleError(
            f"contrib dependency cycle prevents startup: {'; '.join(details)}",
            tuple(pending),
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class _Diff:
    registrations: tuple[_Registration, ...]
    closed: tuple[Enablement, ...]
    affected: frozenset[_ContribInstance]
    retained: dict[UUID, _ContribInstance]


def target(hub: ContribHub, owner: Enablement, composition: Composition) -> Candidate:
    """Validate the complete instance graph, reusing matching registrations."""
    if not isinstance(hub._state, _Open) or not isinstance(owner._state, _Open):
        raise ContribError("the hub or composition is stopping")
    composition = Composition.model_validate(composition)
    remaining = list(owner._registrations.values())
    records = []
    diagnostics = list(composition.diagnostics)
    context = composition.model_copy(
        update={
            "entries": (
                *(
                    record.entry
                    for record in hub._registrations.values()
                    if record.owner is not owner
                ),
                *composition.entries,
            )
        }
    )
    for entry in composition.entries:
        try:
            config = entry.definition.validate_config(entry.config, context=context)
        except ValueError as error:
            if not composition.isolate_errors and composition.roots is None:
                raise ContribError(
                    f"contrib {entry.name!r} at {entry.location}: invalid configuration: {error}"
                ) from error
            diagnostics.append(
                ContribStatus(
                    uuid7(),
                    entry.name,
                    entry.location,
                    Failed(error=str(error), phase="stopped"),
                )
            )
            continue
        current = next(
            (
                record
                for record in remaining
                if record.entry.name == entry.name
                and record.entry.definition == entry.definition
                and record.config == config
                and record.entry.local == entry.local
                and record.entry.bindings == entry.bindings
                and tuple(record.entry.required_offers) == tuple(entry.required_offers)
                and record.entry.source == entry.source
            ),
            None,
        )
        if current is None:
            records.append(_Registration(entry, config, owner))
        else:
            remaining.remove(current)
            records.append(replace(current, entry=entry, config=config))
    removed = {record.id for record in remaining}
    closed: set[Enablement] = set()
    while True:
        added = {
            group
            for group in hub._owners.values()
            if group is not owner
            and group not in closed
            and group._parent is not None
            and (
                group._parent.registration.id in removed
                or group._parent.enablement in closed
            )
        }
        if not added:
            break
        closed.update(added)
        removed.update(
            record.id for group in added for record in group._registrations.values()
        )
    outside = [
        record
        for record in hub._registrations.values()
        if record.owner is not owner and record.owner not in closed
    ]
    while True:
        preview = validate(
            hub,
            owner,
            composition.model_copy(update={"diagnostics": tuple(diagnostics)}),
            (*outside, *records),
        )
        changes = diff(hub, owner, preview)
        added = {
            group
            for group in hub._owners.values()
            if group is not owner
            and group not in closed
            and group._parent in changes.affected
        }
        if not added:
            return preview
        closed.update(added)
        outside = [record for record in outside if record.owner not in closed]


def diff(hub: ContribHub, owner: Enablement, preview: Candidate) -> _Diff:
    """Find unchanged instances and those that must stop or restart after an update."""
    fresh = {instance.registration.id: instance for instance in preview._instances}
    current = {instance.registration.id: instance for instance in hub._all_instances()}
    kept: dict[UUID, _ContribInstance] = {}
    for id, instance in fresh.items():
        previous = current.get(id)
        if (
            previous is not None
            and previous.bindings == instance.bindings
            and previous.accepts == instance.accepts
            and previous._dependencies.keys() == instance._dependencies.keys()
            and all(
                previous._dependencies[key].registration.id == provider.registration.id
                for key, provider in instance._dependencies.items()
            )
        ):
            kept[id] = previous
    affected = {
        instance for id, instance in current.items() if kept.get(id) is not instance
    }
    pending = list(affected)
    while pending:
        for instance in hub._dependents.get(pending.pop(), {}).values():
            if instance not in affected:
                affected.add(instance)
                pending.append(instance)
    kept = {id: instance for id, instance in kept.items() if instance not in affected}
    records = {record.id for record in preview._registrations}
    removed = tuple(
        record for record in hub._registrations.values() if record.id not in records
    )
    closed = tuple(
        group
        for group in hub._owners.values()
        if group is not owner and group._parent in affected
    )
    return _Diff(removed, closed, frozenset(affected), kept)
