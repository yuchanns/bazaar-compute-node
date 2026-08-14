from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .app.application import NodeApplication
from .app.config import ConfigurationError, load_node_configuration
from .app.registry import AdapterFactories, AdapterRegistry, ProviderLoadError
from .app.transport import LocalCommandClient, local_endpoint_for_path
from .core.paths import resolve_data_dir
from .core.runtime import RuntimeSandboxMode

DEFAULT_AUDIT = "logging"
DEFAULT_STORAGE = "sqlite"


def build_parser() -> argparse.ArgumentParser:
    default_data_dir = resolve_data_dir()
    parser = argparse.ArgumentParser(
        prog="bcn",
        description=(
            "Runtime-agnostic computer node daemon for agents and channels. "
            f"Persistent node root: {default_data_dir}."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("start", "stop", "restart", "run"),
        help="Daemon command; providing adapter options without a command means start.",
    )
    parser.add_argument("--channel")
    parser.add_argument("--runtime")
    parser.add_argument(
        "--model",
        type=_non_empty_option,
        help="Optional model override passed to the selected runtime.",
    )
    parser.add_argument(
        "--effort",
        type=_non_empty_option,
        help="Optional reasoning effort passed to the selected runtime.",
    )
    parser.add_argument(
        "--sandbox-mode",
        type=RuntimeSandboxMode,
        choices=tuple(RuntimeSandboxMode),
        help="Filesystem sandbox mode applied to runtime turns.",
    )
    parser.add_argument(
        "--network-access",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow runtime commands to access the network.",
    )
    parser.add_argument("--storage")
    parser.add_argument("--audit")
    parser.add_argument(
        "--config",
        type=Path,
        help="Configuration file path; defaults to the node data directory.",
    )
    parser.add_argument(
        "--database-name",
        type=_database_name,
        help="SQLite database filename under the node data directory.",
    )
    parser.add_argument(
        "--endpoint",
        type=Path,
        help="Local command endpoint path on Unix; Windows derives a named pipe.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run the selected node in the current process instead of daemonizing.",
    )
    return parser


def _non_empty_option(value: str) -> str:
    if not value:
        raise argparse.ArgumentTypeError("option value must be non-empty")
    return value


def _database_name(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise argparse.ArgumentTypeError(
            "database name must be a single non-empty path component"
        )
    return value


def _endpoint_path(args: argparse.Namespace, data_dir: Path) -> Path:
    return (args.endpoint or data_dir / "bcn.sock").expanduser()


def _require_adapters(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if args.channel is None or args.runtime is None:
        parser.error("--channel and --runtime must be provided together")


def _load_factories(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> AdapterFactories:
    _require_adapters(parser, args)
    try:
        return AdapterRegistry().load(
            channel=args.channel,
            runtime=args.runtime,
            storage=args.storage,
            audit=args.audit,
            storage_options={"database_name": args.database_name}
            if args.storage == "sqlite" and args.database_name is not None
            else None,
        )
    except ProviderLoadError as error:
        parser.error(str(error))


def _runtime_options(args: argparse.Namespace) -> dict[str, str]:
    return {
        name: value
        for name, value in (
            ("model", args.model),
            ("effort", args.effort),
        )
        if value is not None
    }


def _apply_runtime_configuration(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    try:
        configuration = load_node_configuration(args.config)
    except ConfigurationError as error:
        parser.error(str(error))
    for name in (
        "channel",
        "runtime",
        "storage",
        "audit",
        "model",
        "effort",
        "sandbox_mode",
        "network_access",
    ):
        if getattr(args, name) is None:
            setattr(args, name, getattr(configuration, name))
    if args.endpoint is None and configuration.endpoint is not None:
        args.endpoint = Path(configuration.endpoint).expanduser()
    if args.database_name is None:
        args.database_name = configuration.database_name
    if args.storage is None:
        args.storage = DEFAULT_STORAGE
    if args.audit is None:
        args.audit = DEFAULT_AUDIT
    args.runtime_env_include = configuration.runtime_env_include
    args.runtime_idle_timeout_seconds = configuration.runtime_idle_timeout_seconds
    args.channel_options = {
        "bot_id": configuration.wecom_bot_id,
        "websocket_url": configuration.wecom_websocket_url,
    }


async def _run_node(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    factories = await asyncio.to_thread(_load_factories, args, parser)

    data_dir = resolve_data_dir()
    node = NodeApplication(
        factories=factories,
        endpoint_path=_endpoint_path(args, data_dir),
        runtime_options=_runtime_options(args),
        runtime_sandbox_mode=args.sandbox_mode,
        runtime_network_access=args.network_access,
        runtime_idle_timeout_seconds=args.runtime_idle_timeout_seconds,
        channel_options=args.channel_options if args.channel == "wecom" else {},
        runtime_environment_include=args.runtime_env_include,
    )
    await node.start()
    print(
        f"bcn ready channel={args.channel} runtime={args.runtime} endpoint={node.endpoint}",
        flush=True,
    )
    try:
        await node.wait()
    finally:
        await node.stop()
    return 0


def _daemon_command(args: argparse.Namespace, data_dir: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "bazaar_compute_node.cli",
        "run",
        "--channel",
        args.channel,
        "--runtime",
        args.runtime,
        "--storage",
        args.storage,
        "--audit",
        args.audit,
        "--endpoint",
        str(_endpoint_path(args, data_dir)),
    ]
    if args.config is not None:
        command.extend(("--config", str(args.config)))
    if args.database_name is not None:
        command.extend(("--database-name", args.database_name))
    for name in ("model", "effort", "sandbox_mode"):
        value = getattr(args, name)
        if value is not None:
            command.extend((f"--{name.replace('_', '-')}", value))
    command.append("--network-access" if args.network_access else "--no-network-access")
    return command


def _spawn_daemon(
    command: Sequence[str],
    log_path: Path,
) -> subprocess.Popen[bytes]:
    with log_path.open("ab") as log_file:
        if os.name == "nt":
            return subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=(
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
            )
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )


async def _endpoint_is_reachable(
    endpoint: str,
    *,
    timeout: float,
) -> bool:
    try:
        response = await LocalCommandClient.request(
            endpoint,
            {"kind": "control", "operation": "health"},
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        return False
    return response.get("ok") is True


async def _wait_for_endpoint(
    endpoint: str,
    process: subprocess.Popen[bytes],
    *,
    timeout: float,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await _endpoint_is_reachable(endpoint, timeout=0.5):
            return
        if process.poll() is not None:
            raise RuntimeError(
                f"daemon exited before becoming ready; see "
                f"{resolve_data_dir() / 'bcn.log'}"
            )
        await asyncio.sleep(0.05)
    raise TimeoutError(f"daemon did not become ready within {timeout:g} seconds")


async def _wait_for_endpoint_exit(
    endpoint: str,
    *,
    timeout: float,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not await _endpoint_is_reachable(endpoint, timeout=0.5):
            return True
        await asyncio.sleep(0.05)
    return not await _endpoint_is_reachable(endpoint, timeout=0.5)


async def _start_daemon(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    await asyncio.to_thread(_load_factories, args, parser)
    data_dir = await asyncio.to_thread(resolve_data_dir)
    await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True)
    endpoint_path = _endpoint_path(args, data_dir)
    endpoint = local_endpoint_for_path(endpoint_path)
    if os.name == "nt" and await _endpoint_is_reachable(endpoint, timeout=0.5):
        parser.error("bcn is already running")
    if os.name != "nt" and await asyncio.to_thread(endpoint_path.exists):
        parser.error(f"bcn endpoint already exists: {endpoint_path}")

    log_path = data_dir / "bcn.log"
    daemon_command = _daemon_command(args, data_dir)
    process = await asyncio.to_thread(_spawn_daemon, daemon_command, log_path)
    try:
        await _wait_for_endpoint(endpoint, process, timeout=10)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            await asyncio.to_thread(process.wait, 5)
        raise
    print(
        f"bcn started pid={process.pid} channel={args.channel} "
        f"runtime={args.runtime} endpoint={endpoint}",
        flush=True,
    )
    return 0


async def _stop_daemon(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    data_dir = await asyncio.to_thread(resolve_data_dir)
    endpoint_path = _endpoint_path(args, data_dir)
    endpoint = local_endpoint_for_path(endpoint_path)
    if not await _endpoint_is_reachable(endpoint, timeout=0.5):
        print("bcn is not running", flush=True)
        return 0

    try:
        response = await LocalCommandClient.request(
            endpoint,
            {"kind": "control", "operation": "shutdown"},
            timeout=5,
        )
    except Exception as error:  # noqa: BLE001
        parser.error(f"cannot reach bcn daemon: {error}")
    if response.get("ok") is not True:
        parser.error(
            f"bcn daemon rejected shutdown: {response.get('code', 'COMMAND_FAILED')}"
        )
    if not await _wait_for_endpoint_exit(endpoint, timeout=10):
        parser.error(f"bcn endpoint did not stop within 10 seconds: {endpoint}")
    print(f"bcn stopped endpoint={endpoint}", flush=True)
    return 0


async def _restart_daemon(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    if (args.channel is None) != (args.runtime is None):
        parser.error("--channel and --runtime must be provided together")
    if args.channel is None or args.runtime is None:
        parser.error("restart requires --channel and --runtime when config is missing")
    await _stop_daemon(args, parser)
    return await _start_daemon(args, parser)


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser, args = await asyncio.to_thread(_prepare_cli_arguments, argv)
    command = args.command
    if command is None:
        if args.channel is None and args.runtime is None:
            parser.print_help()
            return 0
        command = "start"
    if command == "run" or (command == "start" and args.foreground):
        return await _run_node(args, parser)
    if command == "start":
        return await _start_daemon(args, parser)
    if command == "stop":
        return await _stop_daemon(args, parser)
    if command == "restart":
        return await _restart_daemon(args, parser)
    parser.error(f"unsupported command: {command}")


def _prepare_cli_arguments(
    argv: Sequence[str] | None,
) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config is not None:
        args.config = args.config.expanduser().resolve()
    _apply_runtime_configuration(args, parser)
    return parser, args


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
