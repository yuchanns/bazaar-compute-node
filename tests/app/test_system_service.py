from __future__ import annotations

import locale
import subprocess
from argparse import ArgumentParser, Namespace
from pathlib import Path
from unittest.mock import call, patch

import pytest

from bazaar_compute_node import cli
from bazaar_compute_node.app import system_service
from bazaar_compute_node.core.restart import RESTART_EXIT_CODE


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


def test_system_service_parser_supports_install_start_and_status() -> None:
    parser = system_service.build_system_service_parser()

    install = parser.parse_args(["install", "--env-file", "/tmp/bcn.env"])
    start = parser.parse_args(["start"])
    stop = parser.parse_args(["stop"])
    restart = parser.parse_args(["restart"])
    status = parser.parse_args(["status"])

    assert install.system_service_command == "install"
    assert install.env_file == Path("/tmp/bcn.env")
    assert start.system_service_command == "start"
    assert stop.system_service_command == "stop"
    assert restart.system_service_command == "restart"
    assert status.system_service_command == "status"


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


def test_native_command_hides_windows_console() -> None:
    completed = subprocess.CompletedProcess(
        ["native-service"],
        0,
        stdout="ok",
        stderr="",
    )

    with (
        patch.object(system_service.os, "name", "nt"),
        patch.object(
            system_service.subprocess,
            "CREATE_NO_WINDOW",
            0x08000000,
            create=True,
        ),
        patch.object(
            system_service.subprocess,
            "run",
            return_value=completed,
        ) as run,
    ):
        result = system_service._run_native_command(["native-service"])

    assert result is completed
    run.assert_called_once_with(
        ["native-service"],
        capture_output=True,
        check=False,
        text=True,
        encoding=locale.getencoding(),
        errors="replace",
        creationflags=0x08000000,
    )


def test_managed_file_marker_accepts_utf16_content(tmp_path: Path) -> None:
    path = tmp_path / "managed.xml"
    path.write_bytes(system_service.MANAGED_MARKER.encode("utf-16"))

    system_service._remove_managed_file(path)

    assert not path.exists()


def test_windows_user_resolution_falls_back_to_whoami() -> None:
    with (
        patch.object(system_service.getpass, "getuser", side_effect=OSError),
        patch.object(system_service.platform, "system", return_value="Windows"),
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


def test_macos_status_reports_running_launchd_state(tmp_path: Path) -> None:
    plist_path = tmp_path / "bcn.plist"
    plist_path.write_bytes(b"plist")

    with (
        patch.object(
            system_service,
            "_launchd_paths",
            return_value=(plist_path, tmp_path / "bcn-run.sh"),
        ),
        patch.object(system_service.os, "getuid", return_value=501, create=True),
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


def test_linux_lifecycle_delegates_to_systemd() -> None:
    # start
    with patch.object(system_service, "_run_native_command") as run_native:
        system_service._start_linux()

    run_native.assert_called_once_with(
        ["systemctl", "--user", "start", system_service.SYSTEMD_UNIT_NAME]
    )

    # stop
    with patch.object(system_service, "_run_native_command") as run_native:
        system_service._stop_linux()

    run_native.assert_called_once_with(
        ["systemctl", "--user", "stop", system_service.SYSTEMD_UNIT_NAME],
        check=False,
    )

    # restart
    with patch.object(system_service, "_run_native_command") as run_native:
        system_service._restart_linux()

    run_native.assert_called_once_with(
        ["systemctl", "--user", "restart", system_service.SYSTEMD_UNIT_NAME]
    )


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
        patch.object(system_service.os, "getuid", return_value=501, create=True),
        patch.object(
            system_service,
            "_run_native_command",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run_native,
    ):
        system_service._start_macos()

    assert run_native.call_args_list == [
        call(
            ["launchctl", "print", "gui/501/io.github.yuchanns.bazaar-compute-node"],
            check=False,
        ),
        call(
            [
                "launchctl",
                "kickstart",
                "-k",
                "gui/501/io.github.yuchanns.bazaar-compute-node",
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
        patch.object(system_service.os, "getuid", return_value=501, create=True),
        patch.object(
            system_service,
            "_run_native_command",
            side_effect=native_results,
        ) as run_native,
    ):
        system_service._start_macos()

    assert run_native.call_args_list == [
        call(
            ["launchctl", "print", "gui/501/io.github.yuchanns.bazaar-compute-node"],
            check=False,
        ),
        call(["launchctl", "bootstrap", "gui/501", str(plist_path)]),
    ]

    # restart does not bootout before starting
    with (
        patch.object(system_service, "_start_macos") as start,
        patch.object(system_service, "_stop_macos") as stop,
    ):
        system_service._restart_macos()

    start.assert_called_once_with()
    stop.assert_not_called()


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


def test_prepare_system_service_does_not_load_node_runtime_configuration() -> None:
    parser, args = cli._prepare_cli_arguments(["system-service", "start"])

    assert parser.prog == "bcn"
    assert args.command == "system-service"
    assert args.system_service_command == "start"
    assert not hasattr(args, "configuration")


@pytest.mark.asyncio
async def test_system_service_start_waits_for_ready_health(
    service_context: system_service.SystemServiceContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = Namespace(system_service_command="start")
    parser = system_service.build_system_service_parser()

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
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = Namespace(system_service_command="start")
    parser = system_service.build_system_service_parser()

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
        pytest.raises(SystemExit),
    ):
        await system_service.run_system_service_command(args, parser)

    start.assert_not_called()
    assert "endpoint is healthy while the native service is inactive" in (
        capsys.readouterr().err
    )


@pytest.mark.asyncio
async def test_system_service_stop_uses_native_manager(
    service_context: system_service.SystemServiceContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = Namespace(system_service_command="stop")
    parser = system_service.build_system_service_parser()

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
    parser = system_service.build_system_service_parser()

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
@pytest.mark.asyncio
async def test_legacy_lifecycle_commands_print_deprecation_warning(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = cli.build_parser()
    args = parser.parse_args([command])

    async def fake_system_service_command(
        routed_args: Namespace,
        _: ArgumentParser,
    ) -> int:
        assert routed_args.system_service_command == command
        return 0

    with (
        patch.object(cli, "_prepare_cli_arguments", return_value=(parser, args)),
        patch.object(
            cli,
            "run_system_service_command",
            side_effect=fake_system_service_command,
        ),
    ):
        result = await cli.async_main([command])

    assert result == 0
    assert "DeprecationWarning" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["stop", "restart"])
@pytest.mark.asyncio
async def test_legacy_lifecycle_commands_route_to_system_service(
    command: str,
) -> None:
    parser = cli.build_parser()
    args = parser.parse_args([command])

    async def fake_system_service_command(
        routed_args: Namespace,
        _: ArgumentParser,
    ) -> int:
        assert routed_args.system_service_command == command
        return 0

    with (
        patch.object(cli, "_prepare_cli_arguments", return_value=(parser, args)),
        patch.object(
            cli,
            "run_system_service_command",
            side_effect=fake_system_service_command,
        ),
    ):
        result = await cli.async_main([command])

    assert result == 0


def test_windows_wrapper_swaps_the_staged_release_before_starting(
    service_context: system_service.SystemServiceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))

    rendered = system_service._render_windows_wrapper(service_context)

    swap = rendered[
        rendered.index("$previousDirectory = ") : rendered.index("Add-Type")
    ]

    # case: the swap happens before bcn is started, while nothing holds the
    # directory open
    assert rendered.index("$stagingDirectory") < rendered.index(
        "[BcnNoWindowProcess]::Run"
    )
    assert "Move-Item -LiteralPath $stagingDirectory" in swap

    # case: the tool directory is read from the environment on every turn, not
    # baked in when the launcher was written
    assert "$env:UV_TOOL_DIR" in rendered
    assert str(tmp_path / "tools") not in rendered

    # case: an interrupted swap is recovered from either side of the two renames
    assert "-not (Test-Path -LiteralPath $liveDirectory)" in swap

    # case: a rename that fails is logged rather than left to stop the service
    assert "Add-Content -LiteralPath $logPath" in swap
    assert "exit" not in swap

    # case: a swap that fails halfway puts the live directory back, so bcn is
    # not started against a name that no longer exists
    recovery = swap[swap.index("} catch {") :]
    assert "Move-Item -LiteralPath $previousDirectory" in recovery


def test_managed_files_declare_the_revision_they_were_written_from(
    service_context: system_service.SystemServiceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))

    wrapper = system_service._render_windows_wrapper(service_context)
    unit = system_service._render_systemd_unit(service_context)

    # case: every managed file says which template revision produced it
    for rendered in (wrapper, unit):
        assert system_service.installed_template_revision(rendered) == (
            system_service.TEMPLATE_REVISION
        )

    # case: a file written before revisions existed reports none
    assert (
        system_service.installed_template_revision(
            f"# {system_service.MANAGED_MARKER}\nrest"
        )
        is None
    )

    # case: a file we do not manage reports none either
    assert system_service.installed_template_revision("# something else") is None


def test_windows_wrapper_starts_bcn_again_when_it_asks_to_come_back(
    service_context: system_service.SystemServiceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))

    rendered = system_service._render_windows_wrapper(service_context)

    # case: an upgrade cannot restart the node from inside, so the launcher
    # loops on the exit code the node leaves behind
    assert f"$restartExitCode = {RESTART_EXIT_CODE}" in rendered
    assert "while ($exitCode -eq $restartExitCode)" in rendered

    # case: the swap runs at the top of every loop, so the staged release is in
    # place before bcn starts again
    body = rendered[rendered.index("do {") :]
    assert body.index("Invoke-BcnSwap") < body.index("[BcnNoWindowProcess]::Run")

    # case: nothing here ends the scheduled task that owns this process
    assert "schtasks" not in rendered
