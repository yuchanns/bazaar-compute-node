from __future__ import annotations

import locale
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import call, patch

import click
import pytest

from bazaar_compute_node import cli
from bazaar_compute_node.app import system_service
from bazaar_compute_node.cmd.bcn import build_cli
from bazaar_compute_node.cmd.bcn import node as node_commands
from bazaar_compute_node.cmd.bcn import service as service_commands
from bazaar_compute_node.cmd.bcn._runner import UsageReporter
from bazaar_compute_node.i18n import create_translator

linux_only = pytest.mark.skipif(sys.platform != "linux", reason="systemd")
macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="launchd")
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows task scheduler")


GOLDEN = Path(__file__).parent / "golden" / "system_service"


@pytest.fixture
def golden_context() -> system_service.SystemServiceContext:
    """Fixed paths, so what the templates render can be compared byte for byte."""

    data_dir = Path("/home/test-user/.bcn")
    return system_service.SystemServiceContext(
        executable=Path("/usr/local/bin/bcn"),
        config_path=data_dir / "config.toml",
        data_dir=data_dir,
        env_file=data_dir / "runtime.env",
        log_path=data_dir / "system-service.log",
        user="test-user",
    )


@pytest.fixture
def service_context(tmp_path: Path) -> system_service.SystemServiceContext:
    data_dir = tmp_path / ".bcn"
    return system_service.SystemServiceContext(
        executable=tmp_path / "bin" / "bcn",
        config_path=data_dir / "config.toml",
        data_dir=data_dir,
        env_file=data_dir / "runtime.env",
        log_path=data_dir / "system-service.log",
        user="test-user",
    )


def test_system_service_command_tree_carries_install_start_and_status() -> None:
    service = build_cli(create_translator(None)).commands["system-service"]
    assert isinstance(service, click.Group)

    assert sorted(service.commands) == [
        "install",
        "restart",
        "start",
        "status",
        "stop",
        "uninstall",
    ]

    install = service.commands["install"]
    context = click.Context(install)
    install.parse_args(context, ["--env-file", "/tmp/bcn.env"])

    assert context.params["env_file"] == Path("/tmp/bcn.env")


def test_native_command_uses_system_encoding_without_decode_failures() -> None:
    completed = subprocess.CompletedProcess(
        ["native-service"],
        0,
        stdout="ok",
        stderr="",
    )

    with patch.object(
        system_service.subprocess,
        "run",
        return_value=completed,
    ) as run:
        result = system_service._run_native_command(["native-service"])

    assert result is completed
    run.assert_called_once_with(
        ["native-service"],
        capture_output=True,
        check=False,
        text=True,
        encoding=locale.getencoding(),
        errors="replace",
        creationflags=0,
    )


@windows_only
def test_native_command_hides_windows_console() -> None:
    completed = subprocess.CompletedProcess(
        ["native-service"],
        0,
        stdout="ok",
        stderr="",
    )

    with patch.object(
        system_service.subprocess,
        "run",
        return_value=completed,
    ) as run:
        result = system_service._run_native_command(["native-service"])

    assert result is completed
    run.assert_called_once_with(
        ["native-service"],
        capture_output=True,
        check=False,
        text=True,
        encoding=locale.getencoding(),
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def test_managed_file_marker_accepts_utf16_content(tmp_path: Path) -> None:
    path = tmp_path / "managed.xml"
    path.write_bytes(system_service.MANAGED_MARKER.encode("utf-16"))

    system_service._remove_managed_file(path)

    assert not path.exists()


@windows_only
def test_windows_user_resolution_falls_back_to_whoami() -> None:
    with (
        patch.object(system_service.getpass, "getuser", side_effect=OSError),
        patch.object(
            system_service,
            "_run_native_command",
            return_value=subprocess.CompletedProcess(
                ["whoami"],
                0,
                "CONTOSO\\test-user\r\n",
                "",
            ),
        ) as run_native,
    ):
        user = system_service._resolve_current_user()

    assert user == "CONTOSO\\test-user"
    run_native.assert_called_once_with(["whoami"], check=False)


@macos_only
def test_macos_status_reports_running_launchd_state(tmp_path: Path) -> None:
    plist_path = tmp_path / "bcn.plist"
    plist_path.write_bytes(b"plist")

    with (
        patch.object(
            system_service,
            "_launchd_paths",
            return_value=(plist_path, tmp_path / "bcn-run.sh"),
        ),
        patch.object(
            system_service,
            "_run_native_command",
            return_value=subprocess.CompletedProcess(
                ["launchctl"],
                0,
                "state = running\n",
                "",
            ),
        ),
    ):
        status = system_service._status_macos()

    assert status.active is True
    assert status.detail == "launchd state=running"


@windows_only
def test_windows_status_reports_running_task() -> None:
    with patch.object(
        system_service,
        "_run_native_command",
        return_value=subprocess.CompletedProcess(
            ["powershell.exe"],
            0,
            "running\r\n",
            "",
        ),
    ) as run_native:
        status = system_service._status_windows()

    assert status.installed is True
    assert status.active is True
    assert status.detail == "state=running"
    run_native.assert_called_once()
    command = run_native.call_args.args[0]
    assert command[:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
    assert command[3] == "-Command"
    assert "Get-ScheduledTask" in command[4]
    assert "BazaarComputeNode" in command[4]
    assert run_native.call_args.kwargs == {"check": False}


@windows_only
def test_windows_process_query_returns_managed_process_ids(
    service_context: system_service.SystemServiceContext,
) -> None:
    completed = subprocess.CompletedProcess(
        ["powershell.exe"],
        0,
        "12884\r\n12884\r\n12885\r\n",
        "",
    )

    with patch.object(
        system_service,
        "_run_native_command",
        return_value=completed,
    ) as run_native:
        process_ids = system_service._managed_windows_process_ids(service_context)

    assert process_ids == (12884, 12885)
    command = run_native.call_args.args[0]
    assert command[:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
    assert str(service_context.config_path) in command[-1]
    assert "Get-CimInstance -ClassName Win32_Process" in command[-1]


@windows_only
def test_windows_stop_reclaims_orphaned_managed_process(
    service_context: system_service.SystemServiceContext,
) -> None:
    with (
        patch.object(
            system_service,
            "_managed_windows_process_ids",
            side_effect=[(12884,), (12884,), ()],
        ),
        patch.object(
            system_service,
            "_status_windows",
            side_effect=[
                system_service.NativeServiceStatus(
                    True,
                    True,
                    True,
                    "state=running",
                ),
                system_service.NativeServiceStatus(
                    True,
                    True,
                    False,
                    "state=ready",
                ),
            ],
        ),
        patch.object(system_service, "_run_native_command") as run_native,
        patch.object(system_service.time, "sleep"),
    ):
        system_service._stop_windows(service_context)

    assert run_native.call_args_list == [
        call(
            ["schtasks", "/End", "/TN", system_service.WINDOWS_TASK_NAME],
            check=False,
        ),
        call(["taskkill", "/PID", "12884", "/T", "/F"], check=False),
        call(["taskkill", "/PID", "12884", "/T", "/F"], check=False),
    ]


@windows_only
def test_windows_stop_fails_when_managed_process_survives(
    service_context: system_service.SystemServiceContext,
) -> None:
    with (
        patch.object(
            system_service,
            "_managed_windows_process_ids",
            side_effect=[(12884,), (12884,), (12884,)],
        ),
        patch.object(
            system_service,
            "_status_windows",
            return_value=system_service.NativeServiceStatus(
                True,
                True,
                True,
                "state=running",
            ),
        ),
        patch.object(system_service, "_run_native_command"),
        patch.object(system_service.time, "sleep"),
        patch.object(system_service, "WINDOWS_STOP_ATTEMPTS", 2),
        pytest.raises(
            RuntimeError,
            match="Windows native service did not stop.*remaining_pids=12884",
        ),
    ):
        system_service._stop_windows(service_context)


@pytest.mark.skipif(os.sep != "/", reason="golden paths are posix")
def test_rendered_service_files_match_their_golden_copies(
    golden_context: system_service.SystemServiceContext,
) -> None:
    data_dir = golden_context.data_dir
    rendered: dict[str, str | bytes] = {
        "systemd.service": system_service._render_systemd_unit(golden_context),
        "launchd.sh": system_service._render_launchd_wrapper(),
        "launchd.plist": system_service._render_launchd_plist(
            golden_context, data_dir / "bcn-run.sh"
        ),
        "windows.ps1": system_service._render_windows_wrapper(golden_context),
        "windows.vbs": system_service._render_windows_launcher(
            data_dir / "bcn-run.ps1"
        ),
        "windows.xml": system_service._render_windows_task(
            golden_context, data_dir / "bcn-run.vbs"
        ),
    }

    for name, value in rendered.items():
        golden = GOLDEN / name
        if isinstance(value, bytes):
            assert value == golden.read_bytes(), name
        else:
            assert value == golden.read_text(encoding="utf-8"), name


@macos_only
def test_macos_launchd_lifecycle(tmp_path: Path) -> None:
    # a loaded job is kickstarted
    plist_path = tmp_path / "bcn.plist"
    plist_path.write_bytes(b"plist")
    wrapper_path = tmp_path / "bcn-run.sh"

    with (
        patch.object(
            system_service,
            "_launchd_paths",
            return_value=(plist_path, wrapper_path),
        ),
        patch.object(
            system_service,
            "_run_native_command",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run_native,
    ):
        system_service._start_macos()

    assert run_native.call_args_list == [
        call(
            [
                "launchctl",
                "print",
                f"gui/{os.getuid()}/io.github.yuchanns.bazaar-compute-node",
            ],
            check=False,
        ),
        call(
            [
                "launchctl",
                "kickstart",
                "-k",
                f"gui/{os.getuid()}/io.github.yuchanns.bazaar-compute-node",
            ]
        ),
    ]

    # an unloaded job is bootstrapped
    plist_path = tmp_path / "bcn.plist"
    plist_path.write_bytes(b"plist")
    wrapper_path = tmp_path / "bcn-run.sh"
    native_results = [
        subprocess.CompletedProcess([], 1, "", "not loaded"),
        subprocess.CompletedProcess([], 0, "", ""),
    ]

    with (
        patch.object(
            system_service,
            "_launchd_paths",
            return_value=(plist_path, wrapper_path),
        ),
        patch.object(
            system_service,
            "_run_native_command",
            side_effect=native_results,
        ) as run_native,
    ):
        system_service._start_macos()

    assert run_native.call_args_list == [
        call(
            [
                "launchctl",
                "print",
                f"gui/{os.getuid()}/io.github.yuchanns.bazaar-compute-node",
            ],
            check=False,
        ),
        call(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)]),
    ]

    # restart does not bootout before starting
    with (
        patch.object(system_service, "_start_macos") as start,
        patch.object(system_service, "_stop_macos") as stop,
    ):
        system_service._restart_macos()

    start.assert_called_once_with()
    stop.assert_not_called()


@linux_only
def test_linux_install_registers_without_start(
    service_context: system_service.SystemServiceContext,
    tmp_path: Path,
) -> None:
    unit_path = tmp_path / "systemd" / "bcn.service"
    native_commands: list[list[str]] = []

    with (
        patch.object(system_service, "_systemd_unit_path", return_value=unit_path),
        patch.object(
            system_service,
            "_run_native_command",
            side_effect=lambda command, **_: native_commands.append(command),
        ),
    ):
        system_service._install_linux(service_context)

    assert native_commands == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", system_service.SYSTEMD_UNIT_NAME],
    ]


def test_system_service_does_not_load_node_runtime_configuration() -> None:
    routed: list[str] = []

    async def record(args: Namespace, _: object) -> int:
        routed.append(args.system_service_command)
        return 0

    # managing the host service has to work before a node is configured at all
    with (
        patch.object(
            service_commands, "run_system_service_command", side_effect=record
        ),
        patch.object(
            node_commands,
            "load_node_configuration",
            side_effect=AssertionError("configuration must not be loaded"),
        ),
    ):
        assert cli.main(["system-service", "start"]) == 0

    assert routed == ["start"]


@pytest.mark.asyncio
async def test_system_service_start_waits_for_ready_health(
    service_context: system_service.SystemServiceContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = Namespace(system_service_command="start")
    parser = UsageReporter()

    with (
        patch.object(system_service, "_build_context", return_value=service_context),
        patch.object(
            system_service,
            "_native_status",
            side_effect=[
                system_service.NativeServiceStatus(
                    True,
                    True,
                    False,
                    "registered",
                ),
                system_service.NativeServiceStatus(
                    True,
                    True,
                    True,
                    "running",
                ),
            ],
        ),
        patch.object(system_service, "_start") as start,
        patch.object(
            system_service,
            "_bcn_health",
            side_effect=["unreachable", "ready"],
        ),
    ):
        result = await system_service.run_system_service_command(args, parser)

    assert result == 0
    start.assert_called_once_with()
    assert "system service started" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_system_service_start_rejects_external_healthy_endpoint(
    service_context: system_service.SystemServiceContext,
) -> None:
    args = Namespace(system_service_command="start")
    parser = UsageReporter()

    with (
        patch.object(system_service, "_build_context", return_value=service_context),
        patch.object(
            system_service,
            "_native_status",
            return_value=system_service.NativeServiceStatus(
                True,
                True,
                False,
                "state=ready",
            ),
        ),
        patch.object(system_service, "_start") as start,
        patch.object(system_service, "_bcn_health", return_value="ready"),
        pytest.raises(click.UsageError, match="endpoint is healthy"),
    ):
        await system_service.run_system_service_command(args, parser)

    start.assert_not_called()


@pytest.mark.asyncio
async def test_system_service_stop_uses_native_manager(
    service_context: system_service.SystemServiceContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = Namespace(system_service_command="stop")
    parser = UsageReporter()

    with (
        patch.object(system_service, "_build_context", return_value=service_context),
        patch.object(
            system_service,
            "_native_status",
            return_value=system_service.NativeServiceStatus(
                True,
                True,
                True,
                "registered",
            ),
        ),
        patch.object(system_service, "_stop") as stop,
    ):
        result = await system_service.run_system_service_command(args, parser)

    assert result == 0
    stop.assert_called_once_with(service_context)
    assert "system service stopped" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_system_service_restart_waits_for_ready_health(
    service_context: system_service.SystemServiceContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = Namespace(system_service_command="restart")
    parser = UsageReporter()

    with (
        patch.object(system_service, "_build_context", return_value=service_context),
        patch.object(
            system_service,
            "_native_status",
            return_value=system_service.NativeServiceStatus(
                True,
                True,
                True,
                "registered",
            ),
        ),
        patch.object(system_service, "_restart") as restart,
        patch.object(system_service, "_bcn_health", return_value="ready"),
    ):
        result = await system_service.run_system_service_command(args, parser)

    assert result == 0
    restart.assert_called_once_with(service_context)
    assert "system service restarted" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["start", "stop", "restart"])
def test_legacy_lifecycle_commands_forward_to_the_service_manager(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    routed: list[str] = []

    async def record(args: Namespace, _: object) -> int:
        routed.append(args.system_service_command)
        return 0

    with patch.object(node_commands, "run_system_service_command", side_effect=record):
        assert cli.main([command]) == 0

    assert routed == [command]
    assert "DeprecationWarning" in capsys.readouterr().err


def test_windows_wrapper_runs_bcn_and_hands_back_its_exit_code(
    service_context: system_service.SystemServiceContext,
) -> None:
    rendered = system_service._render_windows_wrapper(service_context)

    # case: the launcher's whole job is one run of bcn, and it leaves with
    # whatever bcn left with -- upgrading on Windows is the user's to run, so
    # there is no staged release for the launcher to put in place first
    body = rendered[rendered.index("[BcnNoWindowProcess]::Run") :]
    assert body.strip().endswith("exit $exitCode")

    # case: managed files carry the marker that says who wrote them
    assert rendered.splitlines()[0] == f"# {system_service.MANAGED_MARKER}"
