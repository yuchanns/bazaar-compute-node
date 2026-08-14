from __future__ import annotations

import os
from pathlib import Path

import pytest
from bcn_test_support import isolated_test_environment, temporary_test_directory


def test_temporary_test_directory_uses_system_temp_and_cleans_up() -> None:
    with temporary_test_directory(prefix="bcn-support-") as directory:
        created = directory
        assert created.is_dir()

    assert not created.exists()


def test_isolated_test_environment_scopes_paths_and_process_environment() -> None:
    original = {
        name: os.environ.get(name)
        for name in ("HOME", "USERPROFILE", "CODEX_HOME", "BCN_DATA_NAME")
    }

    with isolated_test_environment(prefix="bcn-support-") as environment:
        root = environment.root
        assert environment.endpoint_path == root / "bcn.sock"
        assert environment.home == root / "home"
        assert environment.codex_home == root / "codex-home"
        assert environment.data_dir == environment.home / ".bcn"
        assert environment.workspace == root / "workspace"
        assert all(
            path.is_dir()
            for path in (
                environment.home,
                environment.codex_home,
                environment.data_dir,
                environment.workspace,
            )
        )
        assert Path.home() == environment.home
        assert os.environ["CODEX_HOME"] == str(environment.codex_home)
        assert os.environ["BCN_DATA_NAME"] == environment.data_dir.name

    assert not root.exists()
    assert {
        name: os.environ.get(name)
        for name in ("HOME", "USERPROFILE", "CODEX_HOME", "BCN_DATA_NAME")
    } == original


@pytest.mark.skipif(os.name == "nt", reason="Windows uses a named pipe endpoint")
def test_isolated_test_environment_rejects_non_portable_unix_endpoint() -> None:
    with (
        pytest.raises(RuntimeError, match="portable AF_UNIX path limit"),
        isolated_test_environment(endpoint_name=f"{'x' * 100}.sock"),
    ):
        pass
