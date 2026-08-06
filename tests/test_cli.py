from __future__ import annotations

import subprocess
import sys

import pytest

from bazaar_compute_node.cli import async_main, main


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


def test_help_works_in_a_real_process() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "bazaar_compute_node.cli", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "usage: bcn" in result.stdout
