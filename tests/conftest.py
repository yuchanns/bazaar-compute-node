from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

_owned_basetemp: Path | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _owned_basetemp

    if config.option.basetemp is not None:
        return

    root = Path("/tmp") if os.name == "posix" else Path(tempfile.gettempdir())
    _owned_basetemp = root / f"bcn-pytest-{os.getpid()}-{uuid4().hex[:8]}"
    config.option.basetemp = _owned_basetemp


def pytest_unconfigure(config: pytest.Config) -> None:
    del config
    if _owned_basetemp is not None:
        shutil.rmtree(_owned_basetemp, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolate_bcn_data(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    data_name = f".bcn-test-{os.getpid()}-{uuid4().hex[:8]}"
    data_dir = Path.home() / data_name
    monkeypatch.setenv("BCN_DATA_NAME", data_name)
    yield
    shutil.rmtree(data_dir, ignore_errors=True)
