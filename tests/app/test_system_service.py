from __future__ import annotations

import asyncio
import json
import os
import plistlib
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from argparse import Namespace
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from bcn_test_support import isolated_test_environment

from bazaar_compute_node import cli
from bazaar_compute_node.app import system_service
from bazaar_compute_node.app.config import load_node_configuration
from bazaar_compute_node.app.server_management import write_env_value
from bazaar_compute_node.app.transport import (
    LocalCommandClient,
    local_endpoint_for_path,
)
from bazaar_compute_node.cmd.bcn._runner import UsageReporter

pytestmark = pytest.mark.system


@pytest.fixture
def registered_service() -> Iterator[system_service.SystemServiceContext]:
    if os.environ.get("CI") != "true":
        pytest.skip("native service tests require an isolated CI runner")
    if os.name == "nt":
        existing = system_service._run_native_command(
            ["schtasks", "/Query", "/TN", system_service.WINDOWS_TASK_NAME], check=False
        )
        if existing.returncode == 0:
            pytest.fail("the isolated runner already has a node task")

    name = f"bcn-system-test-{uuid4().hex}"
    native_environment = os.environ.copy()
    if sys.platform == "linux":
        runtime = f"/run/user/{os.getuid()}"
        native_environment.update(
            XDG_RUNTIME_DIR=runtime,
            DBUS_SESSION_BUS_ADDRESS=f"unix:path={runtime}/bus",
        )

    with isolated_test_environment(prefix="sys-") as isolated:
        data_dir = isolated.root / "installed ${TEAM} %Q" / ".bcn"
        data_dir.mkdir(parents=True)
        config_path = data_dir / "config ${TEAM} %h.toml"
        config_path.write_text(
            'version = "4"\n[node]\nstorage = "sqlite"\nversion_check = false\n'
        )
        env_file = data_dir / (
            "credentials.ps1" if os.name == "nt" else "credentials.env"
        )
        write_env_value(env_file, "BCN_HOME", str(isolated.root / "other home"))
        write_env_value(env_file, "TEAM", "other-directory")
        native_environment.update(
            UV_TOOL_DIR=str(isolated.root / "tools"),
            UV_TOOL_BIN_DIR=str(isolated.root / "bin"),
        )
        uv = shutil.which("uv")
        assert uv is not None
        subprocess.run(
            [
                uv,
                "tool",
                "install",
                "--python",
                vars(sys)["_base_executable"],
                str(Path(__file__).parents[2]),
            ],
            env=native_environment,
            check=True,
        )
        executable = isolated.root / "bin" / ("bcn.exe" if os.name == "nt" else "bcn")
        if os.name != "nt":
            link = data_dir.parent / "bin ${TEAM} %Q" / "bcn"
            link.parent.mkdir()
            link.symlink_to(executable)
            executable = link
        context = system_service.SystemServiceContext(
            executable=executable,
            python=Path(vars(sys)["_base_executable"]).resolve(),
            config_path=config_path,
            data_dir=data_dir,
            env_file=env_file,
            log_path=data_dir / "system-service.log",
            user=system_service._resolve_current_user(),
        )
        domain = ""
        processes: dict[int, tuple[int, str]] = {}
        try:
            if sys.platform == "linux":
                system_service._install_supervisor(context)
                unit = system_service._systemd_unit_path()
                unit.parent.mkdir(parents=True)
                unit.write_text(system_service._render_systemd_unit(context))
                # The same installed unit runs under a unique name on the real user
                # manager, so a developer's existing bcn.service is never targeted.
                temporary_unit = isolated.root / f"{name}.service"
                temporary_unit.write_bytes(unit.read_bytes())
                subprocess.run(
                    ["systemctl", "--user", "link", "--runtime", str(temporary_unit)],
                    env=native_environment,
                    check=True,
                )
                subprocess.run(
                    ["systemctl", "--user", "start", f"{name}.service"],
                    env=native_environment,
                    check=True,
                )
            elif sys.platform == "darwin":
                system_service._install_macos(context)
                plist_path, _ = system_service._launchd_paths()
                with plist_path.open("rb") as stream:
                    job = plistlib.load(stream)
                job["Label"] = name
                temporary_plist = isolated.root / f"{name}.plist"
                with temporary_plist.open("wb") as stream:
                    plistlib.dump(job, stream)
                domain = f"gui/{os.getuid()}"
                if subprocess.run(
                    ["launchctl", "print", domain], capture_output=True, check=False
                ).returncode:
                    domain = f"user/{os.getuid()}"
                subprocess.run(
                    ["launchctl", "bootstrap", domain, str(temporary_plist)], check=True
                )
            elif os.name == "nt":
                system_service._install_windows(context)
                system_service._start_windows()
            else:
                pytest.fail(f"unsupported system service platform: {sys.platform}")
            _check_running(context)
            processes = _service_processes(context)
            yield context
        finally:
            if sys.platform == "linux":
                subprocess.run(
                    ["systemctl", "--user", "stop", f"{name}.service"],
                    env=native_environment,
                    check=True,
                )
                subprocess.run(
                    ["systemctl", "--user", "disable", "--runtime", f"{name}.service"],
                    env=native_environment,
                    check=True,
                )
            elif sys.platform == "darwin" and domain:
                subprocess.run(["launchctl", "bootout", f"{domain}/{name}"], check=True)
            elif os.name == "nt":
                system_service._uninstall_windows(context)
            deadline = time.monotonic() + 10
            while _service_processes(context) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not _service_processes(context), (
                f"service processes survived native stop; started with {processes}"
            )


def _check_running(context: system_service.SystemServiceContext) -> None:
    health = asyncio.run(system_service._wait_for_bcn_health(context, attempts=600))
    if health != "ready":
        detail = (
            context.log_path.read_text(encoding="utf-8", errors="replace")
            if context.log_path.exists()
            else "no service log"
        )
        pytest.fail(f"native node did not become ready: {health}\n{detail}")
    response = asyncio.run(
        LocalCommandClient.request(
            local_endpoint_for_path(context.data_dir / "bcn.sock"),
            {"kind": "control", "operation": "health"},
        )
    )
    result = response["result"]
    assert isinstance(result, dict)
    assert result["ready"]
    assert result["accepting"]
    assert (context.data_dir / "bcn.sqlite3").is_file()
    with closing(sqlite3.connect(context.data_dir / "bcn.sqlite3")) as database:
        assert database.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()


def _service_processes(
    context: system_service.SystemServiceContext,
) -> dict[int, tuple[int, str]]:
    if os.name == "nt":
        script = "\n".join(
            (
                "$queryProcessId = $PID",
                f"$configPath = {system_service._powershell_literal(context.config_path)}",
                "$processes = @(Get-CimInstance Win32_Process | Where-Object {",
                "    $_.ProcessId -ne $queryProcessId -and $_.CommandLine -and",
                "    $_.CommandLine.Contains($configPath)",
                "} | Select-Object ProcessId, ParentProcessId, CommandLine)",
                "ConvertTo-Json -InputObject $processes -Compress",
            )
        )
        result = system_service._run_native_command(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        )
        return {
            record["ProcessId"]: (record["ParentProcessId"], record["CommandLine"])
            for record in json.loads(result.stdout or "[]")
        }
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,args="],
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        int(pid): (int(parent), command)
        for pid, parent, command in (
            line.strip().split(maxsplit=2)
            for line in result.stdout.splitlines()
            if len(line.strip().split(maxsplit=2)) == 3
        )
        if str(context.config_path) in command
    }


def _wait_for_new_child(
    context: system_service.SystemServiceContext,
    supervisor: int,
    previous: int,
    tools: Path,
) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        processes = _service_processes(context)
        if (
            supervisor in processes
            and previous not in processes
            and any(parent == supervisor for parent, _ in processes.values())
            and any(
                str(tools).casefold() in command.casefold()
                for _, command in processes.values()
            )
        ):
            _check_running(context)
            return
        time.sleep(0.1)
    pytest.fail(f"supervisor did not replace its child: {_service_processes(context)}")


@pytest.mark.parametrize("force", (False, True), ids=("normal-exit", "forced-exit"))
def test_native_supervisor_keeps_running_and_recopies_after_child_exit(
    registered_service: system_service.SystemServiceContext, force: bool
) -> None:
    context = registered_service
    processes = _service_processes(context)
    supervisor = next(
        pid
        for pid, (_, command) in processes.items()
        if str(context.data_dir / "supervisor.py") in command
    )
    child = next(pid for pid, (parent, _) in processes.items() if parent == supervisor)
    updated = context.data_dir.parents[1] / "updated"
    environment = os.environ.copy()
    environment.update(
        UV_TOOL_DIR=str(updated / "tools"),
        UV_TOOL_BIN_DIR=str(updated / "bin"),
    )
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run(
        [
            uv,
            "tool",
            "install",
            "--python",
            str(context.python),
            str(Path(__file__).parents[2]),
        ],
        env=environment,
        check=True,
    )
    executable = context.executable
    assert executable is not None
    # A real uv launcher for another environment makes a stale running copy
    # observable in the restarted Python process, on Unix and Windows alike.
    shutil.copy2(
        updated / "bin" / ("bcn.exe" if os.name == "nt" else "bcn"), executable
    )
    if force:
        if os.name == "nt":
            system_service._run_native_command(
                ["taskkill", "/PID", str(child), "/T", "/F"]
            )
        else:
            os.kill(child, signal.SIGKILL)
    else:
        response = asyncio.run(
            LocalCommandClient.request(
                local_endpoint_for_path(context.data_dir / "bcn.sock"),
                {"kind": "control", "operation": "shutdown"},
            )
        )
        assert response["ok"]
    _wait_for_new_child(context, supervisor, child, updated / "tools")


def test_native_supervisor_restarts_after_a_real_uv_reinstall(
    registered_service: system_service.SystemServiceContext,
) -> None:
    context = registered_service
    processes = _service_processes(context)
    supervisor = next(
        pid
        for pid, (_, command) in processes.items()
        if str(context.data_dir / "supervisor.py") in command
    )
    child = next(pid for pid, (parent, _) in processes.items() if parent == supervisor)
    environment = os.environ.copy()
    environment.update(
        UV_TOOL_DIR=str(context.data_dir.parents[1] / "tools"),
        UV_TOOL_BIN_DIR=str(context.data_dir.parents[1] / "bin"),
    )
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run(
        # uv rebuilds and reinstalls explicit local directories, so this really
        # replaces the package and entry points while preserving its interpreter.
        [uv, "tool", "install", str(Path(__file__).parents[2])],
        env=environment,
        check=True,
    )
    response = asyncio.run(
        LocalCommandClient.request(
            local_endpoint_for_path(context.data_dir / "bcn.sock"),
            {"kind": "control", "operation": "shutdown"},
        )
    )
    assert response["ok"]
    _wait_for_new_child(
        context, supervisor, child, context.data_dir.parents[1] / "tools"
    )


def test_native_service_and_reconnect_use_registered_paths_without_bcn_home(
    registered_service: system_service.SystemServiceContext,
) -> None:
    os.environ.pop("BCN_HOME", None)
    args = Namespace(
        config=None,
        storage=None,
        audit=None,
        database_name=None,
        endpoint=None,
        foreground=False,
    )
    context = system_service._build_context(
        args, UsageReporter(), require_executable=False
    )
    _check_running(context)
    cli.main(
        ["server", "connect", "--url", "https://example.org", "--token", "system-token"]
    )
    configuration = load_node_configuration(registered_service.config_path)
    assert "example.org" in str(configuration.control_options["url"])
    assert "system-token" in registered_service.env_file.read_text()


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("CI") != "true",
    reason="requires the isolated Linux runner's UID and privilege tools",
)
def test_connect_preserves_registered_paths_for_a_real_uid_without_an_account(
    registered_service: system_service.SystemServiceContext,
) -> None:
    import pwd

    user = 60001
    with pytest.raises(KeyError):
        pwd.getpwuid(user)
    os.environ.pop("BCN_HOME", None)
    # getpass looks up the real UID. Keep the effective UID as the test-file
    # owner so the real CLI can read the runner's Python and package installation.
    subprocess.run(
        [
            "sudo",
            "setpriv",
            f"--ruid={user}",
            f"--euid={os.getuid()}",
            f"--regid={os.getgid()}",
            "--clear-groups",
            "env",
            "-i",
            f"HOME={Path.home()}",
            f"PATH={os.environ['PATH']}",
            str(Path(sys.executable).parent / "bcn"),
            "server",
            "connect",
            "--url",
            "https://example.org",
            "--token",
            "numeric-uid-token",
        ],
        check=True,
    )
    configuration = load_node_configuration(registered_service.config_path)
    assert "example.org" in str(configuration.control_options["url"])
    assert "numeric-uid-token" in registered_service.env_file.read_text()
