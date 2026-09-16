from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from uuid import uuid4

import pytest
from bcn_test_support import temporary_test_directory

_owned_basetemp: AbstractContextManager[Path] | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _owned_basetemp

    if config.option.basetemp is not None:
        return

    _owned_basetemp = temporary_test_directory(prefix="bcn-pytest-")
    config.option.basetemp = _owned_basetemp.__enter__()


def pytest_unconfigure(config: pytest.Config) -> None:
    del config
    if _owned_basetemp is not None:
        _owned_basetemp.__exit__(None, None, None)


@pytest.fixture
def system_temp_dir() -> Iterator[Path]:
    with temporary_test_directory(prefix="bcn-test-") as directory:
        yield directory


@pytest.fixture(autouse=True)
def isolate_home(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path | None:
    """Keep every test, and the processes it spawns, out of the developer's home.

    A real-provider test keeps its home: the claude and codex children read
    their own credentials from there.
    """

    monkeypatch.setenv("BCN_DATA_NAME", f".bcn-test-{os.getpid()}-{uuid4().hex[:8]}")
    if request.node.get_closest_marker("e2e") is not None:
        return None
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home
