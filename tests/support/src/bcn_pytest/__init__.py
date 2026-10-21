"""Allocate private BCN state before pytest loads conftests or test modules."""

import os
from tempfile import TemporaryDirectory

import pytest


def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    directory = TemporaryDirectory(
        prefix="bcn-", dir="/tmp" if os.name == "posix" else None
    )
    environment = pytest.MonkeyPatch()
    environment.setenv("BCN_HOME", directory.name)
    early_config.add_cleanup(directory.cleanup)
    early_config.add_cleanup(environment.undo)
