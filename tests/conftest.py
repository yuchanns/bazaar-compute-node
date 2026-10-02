from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest
from bcn_test_support import temporary_test_directory
from bcn_test_support.plugin import install

_owned_basetemp: AbstractContextManager[Path] | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _owned_basetemp

    # the test channel, runtime, storage and audit, found by the node the
    # way installed plugins are
    install()

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
) -> Iterator[Path | None]:
    """Keep every test, and the processes it spawns, out of the developer's home.

    A real-provider test keeps its home: the claude and codex children read
    their own credentials from there. BCN state uses a temporary root.
    """

    home = tmp_path_factory.mktemp("node")
    monkeypatch.setenv("BCN_HOME", str(home))
    if request.node.get_closest_marker("e2e") is not None:
        yield None
        return
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    yield home
