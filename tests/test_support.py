from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from bcn_test_support import isolated_test_environment, temporary_test_directory

from bazaar_compute_node.core.paths import resolve_data_dir


def test_temporary_test_directory_uses_system_temp_and_cleans_up() -> None:
    with temporary_test_directory(prefix="bcn-support-") as directory:
        created = directory
        assert created.is_dir()

    assert not created.exists()


def test_isolated_test_environment_scopes_paths_and_process_environment() -> None:
    with isolated_test_environment(prefix="bcn-support-") as outer:
        with isolated_test_environment(prefix="bcn-support-") as environment:
            assert all(
                path.is_dir() and path.is_relative_to(environment.root)
                for path in (
                    environment.home,
                    environment.codex_home,
                    environment.data_dir,
                    environment.workspace,
                )
            )
            assert environment.endpoint_path.parent.is_dir()
            assert environment.endpoint_path.is_relative_to(environment.root)
            assert Path.home().is_relative_to(environment.root)
            assert Path(os.environ["CODEX_HOME"]).is_relative_to(environment.root)
            assert resolve_data_dir().is_relative_to(environment.root)
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from bazaar_compute_node.app.config import load_node_configuration; "
                        "load_node_configuration()"
                    ),
                ],
                check=True,
                capture_output=True,
            )
            assert (environment.root / ".bcn" / "config.toml").is_file()

        assert not environment.root.exists()
        assert Path.home().is_relative_to(outer.root)
        assert Path(os.environ["CODEX_HOME"]).is_relative_to(outer.root)
        assert resolve_data_dir().is_relative_to(outer.root)
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from bazaar_compute_node.app.config import load_node_configuration; "
                    "load_node_configuration()"
                ),
            ],
            check=True,
            capture_output=True,
        )
        assert (outer.root / ".bcn" / "config.toml").is_file()

    assert not outer.root.exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows uses a named pipe endpoint")
def test_isolated_test_environment_rejects_non_portable_unix_endpoint() -> None:
    with (
        pytest.raises(RuntimeError, match="portable AF_UNIX path limit"),
        isolated_test_environment(endpoint_name=f"{'x' * 100}.sock"),
    ):
        pass
