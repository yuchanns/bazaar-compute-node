from __future__ import annotations

import argparse
import asyncio
import os
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from . import __version__
from .app.agent_management import build_agent_parser, run_agent_command
from .app.application import NodeApplication
from .app.config import (
    DEFAULT_AUDIT,
    DEFAULT_STORAGE,
    ConfigurationError,
    NodeConfiguration,
    load_control_configuration,
    load_node_configuration,
    resolve_config_path,
)
from .app.registry import AdapterRegistry, ProviderLoadError, SharedAdapterFactories
from .app.transport import LocalCommandClient, local_endpoint_for_path
from .core.paths import resolve_data_dir


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
        choices=("start", "stop", "restart", "run", "agent"),
        help=(
            "Daemon command or Agent configuration management; providing node "
            "options without a command means start."
        ),
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


def _database_name(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise argparse.ArgumentTypeError(
            "database name must be a single non-empty path component"
        )
    return value


def _endpoint_path(args: argparse.Namespace, data_dir: Path) -> Path:
    return (args.endpoint or data_dir / "bcn.sock").expanduser()


def _apply_runtime_configuration(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    raw_storage = args.storage
    raw_audit = args.audit
    raw_endpoint = args.endpoint
    raw_database_name = args.database_name
    try:
        configuration = load_node_configuration(args.config)
    except ConfigurationError as error:
        parser.error(str(error))

    configuration = replace(
        configuration,
        storage=raw_storage or configuration.storage or DEFAULT_STORAGE,
        audit=raw_audit or configuration.audit or DEFAULT_AUDIT,
        endpoint=(
            str(raw_endpoint) if raw_endpoint is not None else configuration.endpoint
        ),
        database_name=raw_database_name or configuration.database_name,
    )
    args.configuration = configuration
    args.storage = configuration.storage
    args.audit = configuration.audit
    args.endpoint = (
        Path(configuration.endpoint).expanduser()
        if configuration.endpoint is not None
        else None
    )
    args.database_name = configuration.database_name


def _apply_control_configuration(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    if args.endpoint is not None:
        return
    try:
        configuration = load_control_configuration(args.config)
    except ConfigurationError as error:
        parser.error(str(error))
    if configuration.endpoint is not None:
        args.endpoint = Path(configuration.endpoint).expanduser()


def _configuration(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> NodeConfiguration:
    configuration = getattr(args, "configuration", None)
    if not isinstance(configuration, NodeConfiguration):
        parser.error("startup configuration has not been loaded")
    return configuration


def _load_shared_factories(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> SharedAdapterFactories:
    try:
        return AdapterRegistry().load_shared(
            storage=args.storage,
            audit=args.audit,
            storage_options={"database_name": args.database_name}
            if args.storage == "sqlite" and args.database_name is not None
            else None,
        )
    except ProviderLoadError as error:
        parser.error(str(error))


def _print_agent_startup_records(records: Sequence[Mapping[str, object]]) -> None:
    for record in records:
        line = (
            f"agent startup id={record.get('agent_id')} name={record.get('name')} "
            f"status={record.get('status')} channel={record.get('channel')} "
            f"runtime={record.get('runtime')}"
        )
        error_type = record.get("error_type")
        error = record.get("error")
        if isinstance(error_type, str) and error_type:
            line += f" error_type={error_type}"
        if isinstance(error, str) and error:
            line += f" error={error}"
        print(line, flush=True)


def _health_count(health: Mapping[str, object], field_name: str) -> int:
    value = health.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"daemon health contains invalid {field_name}")
    return value


def _health_agents(health: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    value = health.get("agents")
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise RuntimeError("daemon health contains invalid agents")
    return tuple(value)


async def _run_node(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    configuration = _configuration(parser, args)
    shared_factories = await asyncio.to_thread(
        _load_shared_factories,
        args,
        parser,
    )
    data_dir = resolve_data_dir()
    node = NodeApplication(
        configuration=configuration,
        shared_factories=shared_factories,
        registry=AdapterRegistry(),
        endpoint_path=_endpoint_path(args, data_dir),
    )
    await node.start()
    records = tuple(
        result.as_health_record()
        for agent in configuration.agents
        if (result := node.agent_startup_results.get(agent.id)) is not None
    )
    _print_agent_startup_records(records)
    print(
        f"bcn ready configured={len(configuration.agents)} "
        f"started={len(node.agents)} "
        f"failed={len(configuration.agents) - len(node.agents)} "
        f"endpoint={node.endpoint}",
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


async def _node_health(
    endpoint: str,
    *,
    timeout: float,
) -> Mapping[str, object] | None:
    try:
        response = await LocalCommandClient.request(
            endpoint,
            {"kind": "control", "operation": "health"},
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        return None
    if response.get("ok") is not True:
        return None
    result = response.get("result")
    return result if isinstance(result, Mapping) else None


async def _endpoint_is_reachable(endpoint: str, *, timeout: float) -> bool:
    return await _node_health(endpoint, timeout=timeout) is not None


async def _wait_for_node_ready(
    endpoint: str,
    process: subprocess.Popen[bytes],
    *,
    timeout: float,
) -> Mapping[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        health = await _node_health(endpoint, timeout=0.5)
        if health is not None and health.get("ready") is True:
            return health
        if process.poll() is not None:
            raise RuntimeError(
                f"daemon exited before becoming ready; see "
                f"{resolve_data_dir() / 'bcn.log'}"
            )
        await asyncio.sleep(0.05)
    raise TimeoutError(f"daemon did not become ready within {timeout:g} seconds")


async def _wait_for_endpoint_exit(endpoint: str, *, timeout: float) -> bool:
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
    _configuration(parser, args)
    await asyncio.to_thread(_load_shared_factories, args, parser)
    data_dir = await asyncio.to_thread(resolve_data_dir)
    await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True)
    endpoint_path = _endpoint_path(args, data_dir)
    endpoint = local_endpoint_for_path(endpoint_path)
    if platform.system() == "Windows" and await _endpoint_is_reachable(
        endpoint, timeout=0.5
    ):
        parser.error("bcn is already running")
    if os.name != "nt" and await asyncio.to_thread(endpoint_path.exists):
        parser.error(f"bcn endpoint already exists: {endpoint_path}")

    log_path = data_dir / "bcn.log"
    process = await asyncio.to_thread(
        _spawn_daemon,
        _daemon_command(args, data_dir),
        log_path,
    )
    try:
        health = await _wait_for_node_ready(endpoint, process, timeout=10)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            await asyncio.to_thread(process.wait, 5)
        raise
    _print_agent_startup_records(_health_agents(health))
    print(
        f"bcn started pid={process.pid} "
        f"configured={_health_count(health, 'configured')} "
        f"started={_health_count(health, 'started_agents')} "
        f"failed={_health_count(health, 'failed_agents')} "
        f"endpoint={endpoint}",
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
    await _stop_daemon(args, parser)
    return await _start_daemon(args, parser)


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser, args = await asyncio.to_thread(_prepare_cli_arguments, argv)
    command = args.command
    if command is None:
        configuration = getattr(args, "configuration", None)
        if not isinstance(configuration, NodeConfiguration) or not configuration.agents:
            parser.print_help()
            return 0
        command = "start"
    if command == "agent":
        return await asyncio.to_thread(run_agent_command, args, parser)
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
    args, remaining = parser.parse_known_args(argv)
    if args.command == "agent":
        agent_args = build_agent_parser().parse_args(remaining)
        vars(args).update(vars(agent_args))
    elif remaining:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")

    if args.config is not None:
        args.config = args.config.expanduser().resolve()

    if args.command == "agent":
        return parser, args
    if args.command == "stop":
        _apply_control_configuration(args, parser)
        return parser, args

    config_path = args.config or resolve_config_path()
    should_prepare_startup = (
        args.command in {"start", "restart", "run"}
        or config_path.exists()
        or any(
            value is not None
            for value in (
                args.storage,
                args.audit,
                args.endpoint,
                args.database_name,
            )
        )
    )
    if should_prepare_startup:
        _apply_runtime_configuration(args, parser)
    return parser, args


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
