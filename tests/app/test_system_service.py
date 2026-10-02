from __future__ import annotations

import asyncio
import os
import plistlib
import sqlite3
import subprocess
import sys
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
        config_path.write_text('version = "4"\n[node]\nstorage = "sqlite"\n')
        env_file = data_dir / (
            "credentials.ps1" if os.name == "nt" else "credentials.env"
        )
        write_env_value(env_file, "BCN_HOME", str(isolated.root / "other home"))
        write_env_value(env_file, "TEAM", "other-directory")
        executable = Path(sys.executable).parent / (
            "bcn.exe" if os.name == "nt" else "bcn"
        )
        if os.name != "nt":
            link = data_dir.parent / "bin ${TEAM} %Q" / "bcn"
            link.parent.mkdir()
            link.symlink_to(executable)
            executable = link
        context = system_service.SystemServiceContext(
            executable=executable,
            config_path=config_path,
            data_dir=data_dir,
            env_file=env_file,
            log_path=data_dir / "system-service.log",
            user=system_service._resolve_current_user(),
        )
        domain = ""
        try:
            if sys.platform == "linux":
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
