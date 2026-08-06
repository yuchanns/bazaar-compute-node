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


def test_help_shows_the_resolved_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    configured_data_dir = tmp_path / "configured-data"
    monkeypatch.setenv("BCN_DATA_DIR", str(configured_data_dir))

    help_text = build_parser().format_help()

    assert str(resolve_data_dir()).replace(" ", "") in help_text.replace(
        " ", ""
    ).replace("\n", "")


def test_help_works_in_a_real_process() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "bazaar_compute_node.cli", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "usage: bcn" in result.stdout
