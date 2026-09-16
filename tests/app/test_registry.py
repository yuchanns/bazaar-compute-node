from __future__ import annotations

from importlib.metadata import distribution

from bcn_test_support import RecordingAudit

from bazaar_compute_node.app.registry import AdapterRegistry

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
        for entry_point in distribution("bcn-test-support").entry_points
        if entry_point.group in PROVIDER_GROUPS
    )

    assert production
    assert test_support
    for entry_point in (*production, *test_support):
        entry_point.load()


def test_audit_options_reach_the_sink_factory() -> None:
    factories = AdapterRegistry().load_shared(
        storage="test",
        audit="test",
        audit_options={"url": "http://127.0.0.1:8765"},
    )

    audit = factories.audit()

    assert isinstance(audit, RecordingAudit)
    assert audit.options == {"url": "http://127.0.0.1:8765"}
