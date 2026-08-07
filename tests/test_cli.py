from __future__ import annotations

import subprocess
import sys

import pytest

from bazaar_compute_node.cli import async_main, build_parser, main
from bazaar_compute_node.core.paths import resolve_data_dir


@pytest.mark.asyncio
async def test_async_main_preserves_help_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = await async_main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "usage: bcn" in captured.out


def test_main_runs_async_entrypoint(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "usage: bcn" in captured.out


def test_help_shows_the_resolved_data_dir() -> None:
    help_text = build_parser().format_help()

    assert str(resolve_data_dir()).replace(" ", "") in help_text.replace(
        " ", ""
    ).replace("\n", "")
    assert "--data-dir" not in help_text


def test_cli_rejects_data_dir_override() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--data-dir", "override"])


def test_cli_defaults_to_sqlite_storage() -> None:
    args = build_parser().parse_args(
        ["run", "--channel", "dummy", "--runtime", "dummy"]
    )

    assert args.storage == "sqlite"


def test_help_works_in_a_real_process() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "bazaar_compute_node.cli", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "usage: bcn" in result.stdout
