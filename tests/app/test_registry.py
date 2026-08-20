from __future__ import annotations

from importlib.metadata import distribution

import pytest

from bazaar_compute_node.app.registry import AdapterRegistry, ProviderLoadError

PROVIDER_GROUPS = frozenset(
    {
        "bazaar_compute_node.audits",
        "bazaar_compute_node.channels",
        "bazaar_compute_node.controls",
        "bazaar_compute_node.runtimes",
        "bazaar_compute_node.storages",
    }
)


def test_unknown_adapter_name_fails_before_composition() -> None:
    with pytest.raises(ProviderLoadError, match="not installed"):
        AdapterRegistry().load_agent(
            channel="missing-channel",
            runtime="test",
            storage="sqlite",
        )


def test_provider_entry_points_keep_test_adapters_out_of_production() -> None:
    production = {
        (entry_point.group, entry_point.name, entry_point.value)
        for entry_point in distribution("bazaar-compute-node").entry_points
        if entry_point.group in PROVIDER_GROUPS
    }
    test_support = {
        (entry_point.group, entry_point.name, entry_point.value)
        for entry_point in distribution("bcn-test-support").entry_points
        if entry_point.group in PROVIDER_GROUPS
    }

    assert production == {
        (
            "bazaar_compute_node.audits",
            "logging",
            "bazaar_compute_node.contrib.logging.plugin:create_audit",
        ),
        (
            "bazaar_compute_node.channels",
            "lark",
            "bazaar_compute_node.contrib.lark.plugin:builder",
        ),
        (
            "bazaar_compute_node.channels",
            "telegram",
            "bazaar_compute_node.contrib.telegram.plugin:builder",
        ),
        (
            "bazaar_compute_node.channels",
            "wecom",
            "bazaar_compute_node.contrib.wecom.plugin:builder",
        ),
        (
            "bazaar_compute_node.runtimes",
            "codex",
            "bazaar_compute_node.contrib.codex.plugin:create_runtime",
        ),
        (
            "bazaar_compute_node.storages",
            "sqlite",
            "bazaar_compute_node.contrib.sqlite.plugin:create_storage",
        ),
    }
    assert test_support == {
        (
            "bazaar_compute_node.audits",
            "test",
            "bcn_test_support.plugin:create_audit",
        ),
        (
            "bazaar_compute_node.channels",
            "test",
            "bcn_test_support.plugin:builder",
        ),
        (
            "bazaar_compute_node.controls",
            "test+test+test",
            "bcn_test_support.plugin:create_control",
        ),
        (
            "bazaar_compute_node.runtimes",
            "test",
            "bcn_test_support.plugin:create_runtime",
        ),
        (
            "bazaar_compute_node.storages",
            "test",
            "bcn_test_support.plugin:create_storage",
        ),
    }
