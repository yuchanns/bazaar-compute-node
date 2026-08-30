from __future__ import annotations

from pathlib import Path

import pytest

from bazaar_compute_node import __distribution__
from bazaar_compute_node.app import system_service, upgrade


def _installed_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str,
) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(upgrade, "windows_wrapper_path", lambda: wrapper)
    wrapper = data_dir / "bcn-system-service.ps1"
    wrapper.write_text(content, encoding="utf-8")
    return wrapper


def test_a_node_without_a_launcher_refuses_to_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "absent.ps1"
    monkeypatch.setattr(upgrade, "windows_wrapper_path", lambda: missing)

    with pytest.raises(upgrade.UpgradeError) as failure:
        upgrade._refresh_windows_wrapper()

    # case: the launcher is what puts the staged release in place, so without
    # one the install would succeed and leave the node on the old version
    assert "system-service install" in str(failure.value)


def test_an_unreadable_launcher_stops_the_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_wrapper(
        tmp_path,
        monkeypatch,
        content=f"# {system_service.MANAGED_MARKER}\n$executable = 'bcn'\n",
    )

    with pytest.raises(upgrade.UpgradeError) as failure:
        upgrade._refresh_windows_wrapper()

    # case: rewriting from a launcher whose environment script cannot be read
    # would leave the node unable to start, so the upgrade stops instead
    assert "environment_script" in str(failure.value)


def test_the_replaced_release_is_kept_until_the_swap_is_proven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    monkeypatch.setenv("UV_TOOL_DIR", str(tools))
    previous = tools / f"{__distribution__}.old"
    previous.mkdir()
    (previous / "marker").write_text("old", encoding="utf-8")
    target = tools / f"{__distribution__}.upgrade-target"
    target.write_text("9.9.9", encoding="utf-8")

    upgrade.discard_replaced_release("0.1.0")

    # case: a node that came up as the old version is still on the old release,
    # so the way back has to stay
    assert previous.is_dir()
    assert target.exists()

    upgrade.discard_replaced_release("9.9.9")

    # case: once the node proves the swap worked, the rollback copy goes
    assert not previous.exists()
    assert not target.exists()


def test_a_node_that_never_upgraded_keeps_its_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    monkeypatch.setenv("UV_TOOL_DIR", str(tools))
    previous = tools / f"{__distribution__}.old"
    previous.mkdir()

    upgrade.discard_replaced_release("0.1.0")

    # case: without a recorded target there was no swap to prove, so nothing
    # this node did not create is removed
    assert previous.is_dir()
