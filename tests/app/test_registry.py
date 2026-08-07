from __future__ import annotations

import pytest

from bazaar_compute_node.app.registry import AdapterRegistry, ProviderLoadError


def test_unknown_adapter_slug_fails_before_composition() -> None:
    with pytest.raises(ProviderLoadError, match="not installed"):
        AdapterRegistry().load(
            channel_slug="missing-channel",
            runtime_slug="dummy",
        )
