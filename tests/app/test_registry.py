from __future__ import annotations

from importlib.metadata import EntryPoint, distribution

import pytest
from bcn_test_support import RecordingAudit
from bcn_test_support.plugin import ENTRY_POINTS

from bazaar_compute_node.app.registry import (
    RUNTIME_ENTRY_POINT_GROUP,
    AdapterRegistry,
    ProviderLoadError,
)
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.observability import AuditContext
from bazaar_compute_node.core.timerwheel import TimerWheel

PROVIDER_GROUPS = frozenset(
    {
        "bazaar_compute_node.audits",
        "bazaar_compute_node.channels",
        "bazaar_compute_node.runtimes",
        "bazaar_compute_node.storages",
    }
)


def test_declared_provider_entry_points_load() -> None:
    production = tuple(
        entry_point
        for entry_point in distribution("bazaar-compute-node").entry_points
        if entry_point.group in PROVIDER_GROUPS
    )
    test_support = tuple(
        entry_point
        for entry_point in ENTRY_POINTS
        if entry_point.group in PROVIDER_GROUPS
    )

    assert production
    assert test_support
    for entry_point in (*production, *test_support):
        entry_point.load()


def test_the_test_plugins_are_not_installed_as_plugins() -> None:
    """The test channel and runtime are found only where the suite puts
    them: installed with the package, a node run from a checkout would
    offer them as real kinds."""

    assert not [
        entry_point
        for entry_point in distribution("bcn-test-support").entry_points
        if entry_point.group in PROVIDER_GROUPS
    ]


def test_audit_options_reach_the_sink_factory() -> None:
    factories = AdapterRegistry().load_shared(storage="test", audit="test")

    audit = factories.audit(
        AuditContext(
            options={"url": "http://127.0.0.1:8765"},
            timer_wheel=TimerWheel(),
            timeout_budget=TimeoutBudget(1, 1, 1, 1),
        )
    )

    assert isinstance(audit, RecordingAudit)
    assert audit.options == {"url": "http://127.0.0.1:8765"}


class _OneRuntimeRegistry(AdapterRegistry):
    """A registry whose only runtime is whatever the test points it at."""

    def __init__(self, value: str) -> None:
        self._entry_point = EntryPoint(
            name="half", value=value, group=RUNTIME_ENTRY_POINT_GROUP
        )

    def _find(self, group: str, name: str) -> EntryPoint | None:
        del group, name
        return self._entry_point

    def runtime_kinds(self) -> tuple[str, ...]:
        return ("half",)


def test_a_runtime_plugin_must_both_build_and_inspect() -> None:
    # a channel builder builds, but cannot say whether it runs here
    registry = _OneRuntimeRegistry(
        "bazaar_compute_node.contrib.telegram.plugin:builder"
    )

    with pytest.raises(ProviderLoadError, match="callable inspect method"):
        registry.runtime_builders()
    with pytest.raises(ProviderLoadError, match="callable inspect method"):
        registry.load_agent(channels=(), runtimes=("half",))


def test_every_installed_runtime_can_be_asked_about_itself() -> None:
    builders = AdapterRegistry().runtime_builders()

    assert set(builders) == set(AdapterRegistry.runtime_kinds())
