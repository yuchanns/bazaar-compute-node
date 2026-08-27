from __future__ import annotations

from importlib.metadata import distribution

PROVIDER_GROUPS = frozenset(
    {
        "bazaar_compute_node.audits",
        "bazaar_compute_node.channels",
        "bazaar_compute_node.controls",
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
