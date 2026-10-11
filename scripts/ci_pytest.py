"""Run package tests with blocking-call detection enabled."""

from __future__ import annotations

import sys
lazy from collections.abc import Iterator

import pytest
lazy from blockbuster import BlockBuster, blockbuster_ctx


@pytest.fixture(autouse=True)
def blocking_calls() -> Iterator[BlockBuster]:
    with blockbuster_ctx() as detector:
        yield detector


if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:], plugins=[sys.modules[__name__]]))
