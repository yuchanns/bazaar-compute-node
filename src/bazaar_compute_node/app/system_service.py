from __future__ import annotations

import argparse
import asyncio
import getpass
import locale
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.paths import resolve_data_dir
from ..i18n import create_translator
from ..rendering import TextTemplate
from .config import ConfigurationError, load_control_configuration, resolve_config_path
from .transport import LocalCommandClient, local_endpoint_for_path
from .usage import Usage

SYSTEMD_UNIT_NAME = "bcn.service"
LAUNCHD_LABEL = "io.github.yuchanns.bazaar-compute-node"
WINDOWS_TASK_NAME = r"\BazaarComputeNode"
MANAGED_MARKER = "Managed by bazaar-compute-node."
WINDOWS_STOP_ATTEMPTS = 100
WINDOWS_STOP_INTERVAL = 0.05

_SYSTEMD_UNIT_TEMPLATE = TextTemplate.from_resource("system_service/systemd.service")
_LAUNCHD_PLIST_TEMPLATE = TextTemplate.from_resource("system_service/launchd.plist")
_LAUNCHD_WRAPPER_TEMPLATE = TextTemplate.from_resource("system_service/launchd.sh")
_WINDOWS_TASK_TEMPLATE = TextTemplate.from_resource("system_service/windows.xml")
_WINDOWS_LAUNCHER_TEMPLATE = TextTemplate.from_resource("system_service/windows.vbs")
_WINDOWS_WRAPPER_TEMPLATE = TextTemplate.from_resource("system_service/windows.ps1")


@dataclass(frozen=True, slots=True)
class SystemServiceContext:
    """Resolved paths used to render one user-level system service."""

    executable: Path | None
    config_path: Path
    data_dir: Path
    env_file: Path | None
    log_path: Path
    user: str


@dataclass(frozen=True, slots=True)
class NativeServiceStatus:
    installed: bool
    enabled: bool | None
    active: bool | None
    detail: str


def _resolve_executable() -> Path:
    executable = shutil.which("bcn")
    if executable is None:
        candidate = Path(sys.argv[0])
        if candidate.is_file():
            executable = str(candidate)
    if executable is None:
        raise RuntimeError(
            "cannot resolve the bcn executable; run this command through the installed bcn CLI"
        )
    return Path(executable).expanduser().resolve()


def _build_context(
    args: argparse.Namespace,
    parser: Usage,
    *,
    require_executable: bool,
) -> SystemServiceContext:
    if (
        args.storage is not None
        or args.audit is not None
        or args.database_name is not None
        or args.endpoint is not None
        or args.foreground
    ):
        parser.error(
            "bcn system-service commands only accept the node-level --config option"
        )
    executable = _resolve_executable() if require_executable else None
    config_path = (args.config or resolve_config_path()).expanduser().resolve()
    env_file = getattr(args, "env_file", None)
    if env_file is not None:
        env_file = env_file.expanduser().resolve()
    data_dir = resolve_data_dir()
    return SystemServiceContext(
        executable=executable,
        config_path=config_path,
        data_dir=data_dir,
        env_file=env_file,
        log_path=data_dir / "system-service.log",
        user=_resolve_current_user(),
    )


def _resolve_current_user() -> str:
    try:
        return getpass.getuser()
    except OSError:
        if platform.system() != "Windows":
            raise RuntimeError("cannot resolve the current user") from None
        result = _run_native_command(["whoami"], check=False)
        user = result.stdout.strip()
        if result.returncode == 0 and user:
            return user
        raise RuntimeError("cannot resolve the current Windows user")


def _require_executable(context: SystemServiceContext) -> Path:
    if context.executable is None:
        raise RuntimeError("the bcn executable is required to render a service")
    return context.executable


def _systemd_quote(path: Path) -> str:
    return shlex.quote(str(path))


def _powershell_literal(path: Path | str | None) -> str:
    value = "" if path is None else str(path)
    return "'" + value.replace("'", "''") + "'"


def _render_systemd_unit(context: SystemServiceContext) -> str:
    return _SYSTEMD_UNIT_TEMPLATE.render(
        {
            "managed_marker": MANAGED_MARKER,
            "executable": _systemd_quote(_require_executable(context)),
            "config_path": _systemd_quote(context.config_path),
            "data_dir": _systemd_quote(context.data_dir),
            "environment_file": (
                "" if context.env_file is None else _systemd_quote(context.env_file)
            ),
        }
    )


def _render_launchd_wrapper() -> str:
    return _LAUNCHD_WRAPPER_TEMPLATE.render({"managed_marker": MANAGED_MARKER})


def _render_launchd_plist(
    context: SystemServiceContext,
    wrapper_path: Path,
) -> bytes:
    return _LAUNCHD_PLIST_TEMPLATE.render(
        {
            "label": LAUNCHD_LABEL,
            "managed_marker": MANAGED_MARKER,
            "wrapper_path": str(wrapper_path),
            "data_dir": str(context.data_dir),
            "config_path": str(context.config_path),
            "environment_file": (
                "" if context.env_file is None else str(context.env_file)
            ),
            "executable": str(_require_executable(context)),
            "log_path": str(context.log_path),
        }
    ).encode("utf-8")


def _render_windows_wrapper(context: SystemServiceContext) -> str:
    return _WINDOWS_WRAPPER_TEMPLATE.render(
        {
            "managed_marker": MANAGED_MARKER,
            "executable": _powershell_literal(_require_executable(context)),
            "config_path": _powershell_literal(context.config_path),
            "environment_script": _powershell_literal(context.env_file),
            "log_path": _powershell_literal(context.log_path),
        }
    )


def _vbs_literal(value: Path | str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _render_windows_launcher(wrapper_path: Path) -> str:
    command = _vbs_literal(
        "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden "
        f'-ExecutionPolicy Bypass -File "{wrapper_path}"'
    )
    return _WINDOWS_LAUNCHER_TEMPLATE.render(
        {
            "managed_marker": MANAGED_MARKER,
            "command": command,
        }
    )


def _render_windows_task(
    context: SystemServiceContext,
    launcher_path: Path,
) -> bytes:
    rendered = _WINDOWS_TASK_TEMPLATE.render(
        {
            "user": context.user,
            "managed_marker": MANAGED_MARKER,
            "arguments": f'//B //Nologo "{launcher_path}"',
            "data_dir": str(context.data_dir),
        }
    )
    return rendered.encode("utf-16")


def _write_managed_file(path: Path, content: str | bytes, *, mode: int) -> None:
    if path.exists():
        if not path.is_file():
            raise RuntimeError(f"managed service path is not a file: {path}")
        existing = path.read_bytes()
        if not _contains_managed_marker(existing):
            raise RuntimeError(f"refusing to overwrite unmanaged file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as temporary_file:
            if isinstance(content, str):
                temporary_file.write(content.encode("utf-8"))
            else:
                temporary_file.write(content)
        if os.name != "nt":
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        temporary_path = None
        if os.name != "nt":
            os.chmod(path, mode)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _remove_managed_file(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_file():
        raise RuntimeError(f"managed service path is not a file: {path}")
    if not _contains_managed_marker(path.read_bytes()):
        raise RuntimeError(f"refusing to remove unmanaged file: {path}")
    path.unlink()


def _contains_managed_marker(content: bytes) -> bool:
    if MANAGED_MARKER.encode("utf-8") in content:
        return True
    for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
        try:
            if MANAGED_MARKER in content.decode(encoding):
                return True
        except UnicodeError:
            continue
    return False


def _run_native_command(
    command: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        encoding=locale.getencoding(),
        errors="replace",
        creationflags=creationflags,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"native service command failed ({result.returncode}): "
            f"{' '.join(command)}{suffix}"
        )
    return result


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME


def _launchd_paths() -> tuple[Path, Path]:
    directory = Path.home() / "Library" / "LaunchAgents"
    return (
        directory / f"{LAUNCHD_LABEL}.plist",
        directory / "bcn-run.sh",
    )


def _windows_paths(context: SystemServiceContext) -> tuple[Path, Path, Path]:
    return (
        context.data_dir / "bcn-system-service.xml",
        context.data_dir / "bcn-system-service.ps1",
        context.data_dir / "bcn-system-service.vbs",
    )


def _install_linux(context: SystemServiceContext) -> None:
    unit_path = _systemd_unit_path()
    _write_managed_file(unit_path, _render_systemd_unit(context), mode=0o600)
    _run_native_command(["systemctl", "--user", "daemon-reload"])
    _run_native_command(["systemctl", "--user", "enable", SYSTEMD_UNIT_NAME])
    print(f"system service installed platform=linux path={unit_path}", flush=True)
    print(
        f"Start with: systemctl --user start {SYSTEMD_UNIT_NAME}",
        flush=True,
    )


def _install_macos(context: SystemServiceContext) -> None:
    plist_path, wrapper_path = _launchd_paths()
    _write_managed_file(
        wrapper_path,
        _render_launchd_wrapper(),
        mode=0o700,
    )
    _write_managed_file(
        plist_path,
        _render_launchd_plist(context, wrapper_path),
        mode=0o600,
    )
    print(f"system service installed platform=macos path={plist_path}", flush=True)
    print(
        f"Start with: launchctl bootstrap gui/$(id -u) {plist_path}",
        flush=True,
    )


def _install_windows(context: SystemServiceContext) -> None:
    xml_path, wrapper_path, launcher_path = _windows_paths(context)
    context.data_dir.mkdir(parents=True, exist_ok=True)
    _write_managed_file(
        wrapper_path,
        _render_windows_wrapper(context),
        mode=0o600,
    )
    _write_managed_file(
        launcher_path,
        _render_windows_launcher(wrapper_path),
        mode=0o600,
    )
    _write_managed_file(
        xml_path,
        _render_windows_task(context, launcher_path),
        mode=0o600,
    )
    _run_native_command(
        [
            "schtasks",
            "/Create",
            "/TN",
            WINDOWS_TASK_NAME,
            "/XML",
            str(xml_path),
            "/F",
        ]
    )
    print(
        f"system service installed platform=windows task={WINDOWS_TASK_NAME}",
        flush=True,
    )
    print(f"Start with: schtasks /Run /TN {WINDOWS_TASK_NAME}", flush=True)


def _install(context: SystemServiceContext) -> None:
    system = platform.system()
    if system == "Linux":
        _install_linux(context)
    elif system == "Darwin":
        _install_macos(context)
    elif system == "Windows":
        _install_windows(context)
    else:
        raise RuntimeError(f"unsupported host service platform: {system}")


def _start_linux() -> None:
    _run_native_command(["systemctl", "--user", "start", SYSTEMD_UNIT_NAME])


def _start_macos() -> None:
    plist_path, _ = _launchd_paths()
    if not plist_path.exists():
        raise RuntimeError(f"system service is not installed: {plist_path}")
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LAUNCHD_LABEL}"
    loaded = _run_native_command(
        ["launchctl", "print", target],
        check=False,
    )
    if loaded.returncode == 0:
        _run_native_command(["launchctl", "kickstart", "-k", target])
    else:
        _run_native_command(["launchctl", "bootstrap", domain, str(plist_path)])


def _start_windows() -> None:
    _run_native_command(["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME])


def _start() -> None:
    system = platform.system()
    if system == "Linux":
        _start_linux()
    elif system == "Darwin":
        _start_macos()
    elif system == "Windows":
        _start_windows()
    else:
        raise RuntimeError(f"unsupported host service platform: {system}")


def _stop_linux() -> None:
    _run_native_command(
        ["systemctl", "--user", "stop", SYSTEMD_UNIT_NAME],
        check=False,
    )


def _stop_macos() -> None:
    _run_native_command(
        [
            "launchctl",
            "bootout",
            f"gui/{os.getuid()}/{LAUNCHD_LABEL}",
        ],
        check=False,
    )


def _managed_windows_process_ids(context: SystemServiceContext) -> tuple[int, ...]:
    config_path = _powershell_literal(context.config_path)
    script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$queryProcessId = $PID",
            f"$configPath = [IO.Path]::GetFullPath({config_path}).ToLowerInvariant()",
            "Get-CimInstance -ClassName Win32_Process |",
            "    Where-Object {",
            "        $_.ProcessId -ne $queryProcessId -and",
            "        $_.CommandLine -and",
            "        $_.CommandLine.ToLowerInvariant().Contains($configPath) -and",
            r"        $_.CommandLine -match '(?i)(^|\s)run(\s|$)' -and",
            r"        $_.CommandLine -match '(?i)(^|\s)--config(\s|$)'",
            "    } |",
            "    ForEach-Object { $_.ProcessId }",
            "",
        )
    )
    result = _run_native_command(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ]
    )
    process_ids: set[int] = set()
    for line in result.stdout.splitlines():
        value = line.strip()
        if value:
            process_ids.add(int(value))
    return tuple(sorted(process_ids))


def _stop_windows(context: SystemServiceContext) -> None:
    tracked_process_ids = set(_managed_windows_process_ids(context))
    _run_native_command(
        ["schtasks", "/End", "/TN", WINDOWS_TASK_NAME],
        check=False,
    )
    for _ in range(WINDOWS_STOP_ATTEMPTS):
        process_ids = set(_managed_windows_process_ids(context))
        tracked_process_ids.update(process_ids)
        for process_id in sorted(tracked_process_ids):
            _run_native_command(
                ["taskkill", "/PID", str(process_id), "/T", "/F"],
                check=False,
            )
        task_status = _status_windows()
        if not process_ids and task_status.active is False:
            return
        time.sleep(WINDOWS_STOP_INTERVAL)
    raise RuntimeError(
        "Windows native service did not stop: "
        f"remaining_pids={','.join(str(pid) for pid in sorted(process_ids))} "
        f"detail={task_status.detail}"
    )


def _stop(context: SystemServiceContext) -> None:
    system = platform.system()
    if system == "Linux":
        _stop_linux()
    elif system == "Darwin":
        _stop_macos()
    elif system == "Windows":
        _stop_windows(context)
    else:
        raise RuntimeError(f"unsupported host service platform: {system}")


def _restart_linux() -> None:
    _run_native_command(["systemctl", "--user", "restart", SYSTEMD_UNIT_NAME])


def _restart_macos() -> None:
    _start_macos()


def _restart_windows(context: SystemServiceContext) -> None:
    _stop_windows(context)
    _start_windows()


def _restart(context: SystemServiceContext) -> None:
    system = platform.system()
    if system == "Linux":
        _restart_linux()
    elif system == "Darwin":
        _restart_macos()
    elif system == "Windows":
        _restart_windows(context)
    else:
        raise RuntimeError(f"unsupported host service platform: {system}")


def _uninstall_linux() -> None:
    unit_path = _systemd_unit_path()
    if not unit_path.exists():
        print("system service is not installed", flush=True)
        return
    _run_native_command(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME])
    _remove_managed_file(unit_path)
    _run_native_command(["systemctl", "--user", "daemon-reload"])
    print(f"system service uninstalled platform=linux path={unit_path}", flush=True)


def _uninstall_macos() -> None:
    plist_path, wrapper_path = _launchd_paths()
    if not plist_path.exists() and not wrapper_path.exists():
        print("system service is not installed", flush=True)
        return
    _run_native_command(
        [
            "launchctl",
            "bootout",
            f"gui/{os.getuid()}/{LAUNCHD_LABEL}",
        ],
        check=False,
    )
    _remove_managed_file(plist_path)
    _remove_managed_file(wrapper_path)
    print(f"system service uninstalled platform=macos path={plist_path}", flush=True)


def _uninstall_windows(context: SystemServiceContext) -> None:
    xml_path, wrapper_path, launcher_path = _windows_paths(context)
    _stop_windows(context)
    _run_native_command(
        ["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"],
        check=False,
    )
    _remove_managed_file(xml_path)
    _remove_managed_file(launcher_path)
    _remove_managed_file(wrapper_path)
    if (
        not xml_path.exists()
        and not launcher_path.exists()
        and not wrapper_path.exists()
    ):
        print("system service uninstalled platform=windows", flush=True)


def _uninstall(context: SystemServiceContext) -> None:
    system = platform.system()
    if system == "Linux":
        _uninstall_linux()
    elif system == "Darwin":
        _uninstall_macos()
    elif system == "Windows":
        _uninstall_windows(context)
    else:
        raise RuntimeError(f"unsupported host service platform: {system}")


def _status_linux() -> NativeServiceStatus:
    unit_path = _systemd_unit_path()
    if not unit_path.exists():
        return NativeServiceStatus(False, False, False, "unit file is absent")
    enabled = _run_native_command(
        ["systemctl", "--user", "is-enabled", SYSTEMD_UNIT_NAME],
        check=False,
    )
    active = _run_native_command(
        ["systemctl", "--user", "is-active", SYSTEMD_UNIT_NAME],
        check=False,
    )
    return NativeServiceStatus(
        True,
        enabled.returncode == 0,
        active.returncode == 0,
        f"enabled={enabled.stdout.strip() or 'unknown'} "
        f"active={active.stdout.strip() or 'unknown'}",
    )


def _status_macos() -> NativeServiceStatus:
    plist_path, _ = _launchd_paths()
    if not plist_path.exists():
        return NativeServiceStatus(False, False, False, "plist is absent")
    loaded = _run_native_command(
        ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
        check=False,
    )
    if loaded.returncode != 0:
        return NativeServiceStatus(True, True, False, "launchd job is not loaded")
    state = _launchd_state(loaded.stdout)
    active = state == "running" if state is not None else None
    detail = f"launchd state={state}" if state is not None else "launchd job"
    return NativeServiceStatus(True, True, active, detail)


def _launchd_state(output: str) -> str | None:
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "state":
            return value.strip().casefold()
    return None


def _status_windows() -> NativeServiceStatus:
    task_path = _powershell_literal("\\")
    task_name = _powershell_literal(WINDOWS_TASK_NAME.lstrip("\\"))
    query_script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            f"$task = Get-ScheduledTask -TaskPath {task_path} -TaskName {task_name}",
            "$task.State.ToString().ToLowerInvariant()",
            "",
        )
    )
    queried = _run_native_command(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            query_script,
        ],
        check=False,
    )
    installed = queried.returncode == 0
    active = _windows_task_active_state(queried.stdout) if installed else False
    detail = "task is absent"
    if installed:
        detail = "registered"
        if active is not None:
            detail = f"state={'running' if active else 'ready'}"
    return NativeServiceStatus(installed, installed, active, detail)


def _windows_task_active_state(output: str) -> bool | None:
    running_states = {"running", "运行中", "正在运行"}
    inactive_states = {
        "disabled",
        "queued",
        "ready",
        "已禁用",
        "就绪",
        "排队",
    }
    for line in output.splitlines():
        state = line.strip().casefold()
        if state in running_states:
            return True
        if state in inactive_states:
            return False
        key, separator, value = line.partition(":")
        if not separator or key.strip().casefold() not in {"status", "task state"}:
            continue
        state = value.strip().casefold()
        if state in running_states:
            return True
        if state in inactive_states:
            return False
    return None


def _native_status() -> NativeServiceStatus:
    system = platform.system()
    if system == "Linux":
        return _status_linux()
    if system == "Darwin":
        return _status_macos()
    if system == "Windows":
        return _status_windows()
    raise RuntimeError(f"unsupported host service platform: {system}")


async def _bcn_health(context: SystemServiceContext) -> str:
    try:
        configuration = load_control_configuration(context.config_path)
    except ConfigurationError as error:
        return f"invalid-config ({error})"
    endpoint_path = (
        Path(configuration.endpoint).expanduser()
        if configuration.endpoint is not None
        else context.data_dir / "bcn.sock"
    )
    try:
        response = await LocalCommandClient.request(
            local_endpoint_for_path(endpoint_path),
            {"kind": "control", "operation": "health"},
            timeout=0.5,
        )
    except Exception:  # noqa: BLE001
        return "unreachable"
    if response.get("ok") is not True:
        return "unhealthy"
    result = response.get("result")
    if not isinstance(result, dict):
        return "invalid-response"
    if result.get("ready") is True and result.get("accepting") is True:
        return "ready"
    return "not-ready"


async def _wait_for_bcn_health(
    context: SystemServiceContext,
    *,
    attempts: int = 200,
) -> str:
    for _ in range(attempts):
        health = await _bcn_health(context)
        if health == "ready" or health.startswith("invalid-config"):
            return health
        await asyncio.sleep(0.05)
    return await _bcn_health(context)


async def _check_endpoint_conflict(
    context: SystemServiceContext,
    native_status: NativeServiceStatus,
) -> str | None:
    if native_status.active is not True and await _bcn_health(context) == "ready":
        return "endpoint is healthy while the native service is inactive"
    return None


async def _wait_for_managed_service_health(context: SystemServiceContext) -> str:
    health = await _wait_for_bcn_health(context)
    if health != "ready":
        return health
    final_status = await asyncio.to_thread(_native_status)
    if final_status.active is not True:
        return f"native service is not active ({final_status.detail})"
    return health


async def run_system_service_command(
    args: argparse.Namespace,
    parser: Usage,
) -> int:
    command = args.system_service_command
    try:
        context = _build_context(
            args,
            parser,
            require_executable=command == "install",
        )
        if command == "install":
            await asyncio.to_thread(context.data_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(_install, context)
            return 0
        if command == "start":
            native_status = await asyncio.to_thread(_native_status)
            if not native_status.installed:
                parser.error(
                    create_translator(None).text("cli.system_service.not_installed")
                )
            conflict = await _check_endpoint_conflict(context, native_status)
            if conflict is not None:
                parser.error(f"system service did not start: {conflict}")
            await asyncio.to_thread(_start)
            health = await _wait_for_managed_service_health(context)
            if health != "ready":
                parser.error(f"system service did not become ready: {health}")
            print(
                f"system service started platform={platform.system().lower()} "
                "health=ready",
                flush=True,
            )
            return 0
        if command == "stop":
            native_status = await asyncio.to_thread(_native_status)
            if not native_status.installed:
                parser.error(
                    create_translator(None).text("cli.system_service.not_installed")
                )
            await asyncio.to_thread(_stop, context)
            print(
                f"system service stopped platform={platform.system().lower()}",
                flush=True,
            )
            return 0
        if command == "restart":
            native_status = await asyncio.to_thread(_native_status)
            if not native_status.installed:
                parser.error(
                    create_translator(None).text("cli.system_service.not_installed")
                )
            conflict = await _check_endpoint_conflict(context, native_status)
            if conflict is not None:
                parser.error(f"system service did not restart: {conflict}")
            await asyncio.to_thread(_restart, context)
            health = await _wait_for_managed_service_health(context)
            if health != "ready":
                parser.error(f"system service did not become ready: {health}")
            print(
                f"system service restarted platform={platform.system().lower()} "
                "health=ready",
                flush=True,
            )
            return 0
        if command == "uninstall":
            await asyncio.to_thread(_uninstall, context)
            return 0
        if command == "status":
            native_status = await asyncio.to_thread(_native_status)
            health = await _bcn_health(context)
            print(
                f"platform={platform.system().lower()} "
                f"installed={'yes' if native_status.installed else 'no'} "
                f"enabled={'yes' if native_status.enabled else 'no'} "
                f"active={'yes' if native_status.active else 'no'} "
                f"health={health} detail={native_status.detail}",
                flush=True,
            )
            return 0
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    raise AssertionError(f"unsupported system-service command: {command}")


__all__ = [
    "LAUNCHD_LABEL",
    "SYSTEMD_UNIT_NAME",
    "WINDOWS_TASK_NAME",
    "SystemServiceContext",
    "run_system_service_command",
]
