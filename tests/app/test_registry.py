from __future__ import annotations

from collections.abc import Sequence

import pytest

from bazaar_compute_node.app.registry import AdapterRegistry, ProviderLoadError
from bazaar_compute_node.core.runtime import RuntimeCommandContext


def test_dummy_adapters_are_loaded_through_entry_points() -> None:
    factories = AdapterRegistry().load(
        channel_slug="dummy",
        runtime_slug="dummy",
        storage_slug="dummy",
        audit_slug="dummy",
    )

    async def run_command(
        _session_id: str,
        _arguments: Sequence[str],
        _body: str | None,
    ) -> None:
        return None

    assert factories.channel().__class__.__name__ == "DummyChannel"
    assert (
        factories.runtime(
            RuntimeCommandContext(run_command=run_command)
        ).__class__.__name__
        == "DummyRuntime"
    )
    assert factories.storage().__class__.__name__ == "DummyStorage"
    assert factories.audit().__class__.__name__ == "DummyAudit"
    assert factories.control is not None


def test_sqlite_storage_composes_without_dummy_control() -> None:
    factories = AdapterRegistry().load(
        channel_slug="dummy",
        runtime_slug="dummy",
        storage_slug="sqlite",
        audit_slug="dummy",
    )

    assert factories.storage().__class__.__name__ == "SqliteDatabase"
    assert factories.control is None


def test_unknown_adapter_slug_fails_before_composition() -> None:
    with pytest.raises(ProviderLoadError, match="not installed"):
        AdapterRegistry().load(
            channel_slug="missing-channel",
            runtime_slug="dummy",
        )
