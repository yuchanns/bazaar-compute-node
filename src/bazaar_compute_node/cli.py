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
from .app.daemon import (
    process_is_alive,
    read_runtime_metadata,
    remove_runtime_metadata,
    wait_for_process_exit,
    wait_for_runtime_metadata,
)
from .app.registry import AdapterFactories, AdapterRegistry, ProviderLoadError
from .app.transport import LocalCommandClient
from .core.paths import resolve_data_dir


def build_parser() -> argparse.ArgumentParser:
    default_data_dir = resolve_data_dir()
    parser = argparse.ArgumentParser(
        prog="bcn",
        description="Runtime-agnostic computer node daemon for agents and channels.",
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
    parser.add_argument("--storage", default="dummy")
    parser.add_argument("--audit", default="dummy")
    parser.add_argument(
        "--data-dir",
        type=Path,
        help=f"Persistent node root (default: {default_data_dir}).",
    )
    parser.add_argument(
        "--endpoint",
        type=Path,
        help="Local command endpoint path on Unix; Windows uses loopback TCP.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run the selected node in the current process instead of daemonizing.",
    )
    return parser


def _data_dir(args: argparse.Namespace) -> Path:
    return resolve_data_dir(args.data_dir)


def _endpoint_path(args: argparse.Namespace, data_dir: Path) -> Path:
    return (args.endpoint or data_dir / "bcn.sock").expanduser()


def _require_adapter_slugs(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if args.channel is None or args.runtime is None:
        parser.error("--channel and --runtime must be provided together")


def _load_factories(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> AdapterFactories:
    _require_adapter_slugs(parser, args)
    try:
        return AdapterRegistry().load(
            channel_slug=args.channel,
            runtime_slug=args.runtime,
            storage_slug=args.storage,
            audit_slug=args.audit,
        )
    except ProviderLoadError as error:
        parser.error(str(error))


async def _run_node(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    factories = _load_factories(args, parser)

    data_dir = _data_dir(args)
    node = NodeApplication(
        factories=factories,
        channel_slug=args.channel,
        runtime_slug=args.runtime,
        storage_slug=args.storage,
        audit_slug=args.audit,
        data_dir=data_dir,
        endpoint_path=_endpoint_path(args, data_dir),
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
    return [
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
        "--data-dir",
        str(data_dir),
        "--endpoint",
        str(_endpoint_path(args, data_dir)),
    ]


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
                close_fds=False,
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


async def _start_daemon(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    _load_factories(args, parser)
    data_dir = _data_dir(args)
    data_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = data_dir / "runtime.json"
    metadata = read_runtime_metadata(metadata_path)
    if metadata is not None:
        if process_is_alive(metadata.pid):
            parser.error(f"bcn is already running with pid {metadata.pid}")
        remove_runtime_metadata(metadata_path)

    log_path = data_dir / "bcn.log"
    daemon_command = _daemon_command(args, data_dir)
    process = await asyncio.to_thread(_spawn_daemon, daemon_command, log_path)
    try:
        metadata = await wait_for_runtime_metadata(
            metadata_path,
            process,
            timeout=10,
        )
    except BaseException:
        if process.poll() is None:
            process.terminate()
            await asyncio.to_thread(process.wait, 5)
        raise
    print(
        f"bcn started pid={metadata.pid} channel={metadata.channel_slug} "
        f"runtime={metadata.runtime_slug} endpoint={metadata.endpoint}",
        flush=True,
    )
    return 0


async def _stop_daemon(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    data_dir = _data_dir(args)
    metadata_path = data_dir / "runtime.json"
    metadata = read_runtime_metadata(metadata_path)
    if metadata is None:
        print("bcn is not running", flush=True)
        return 0
    if not process_is_alive(metadata.pid):
        remove_runtime_metadata(metadata_path)
        print("bcn is not running", flush=True)
        return 0

    try:
        response = await LocalCommandClient.request(
            metadata.endpoint,
            {"kind": "control", "operation": "shutdown"},
            timeout=5,
        )
    except Exception as error:  # noqa: BLE001
        parser.error(f"cannot reach bcn daemon: {error}")
    if response.get("ok") is not True:
        parser.error(
            f"bcn daemon rejected shutdown: {response.get('code', 'COMMAND_FAILED')}"
        )
    if not await wait_for_process_exit(metadata.pid, timeout=10):
        parser.error(f"bcn daemon did not stop within 10 seconds (pid {metadata.pid})")
    print(f"bcn stopped pid={metadata.pid}", flush=True)
    return 0


async def _restart_daemon(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    data_dir = _data_dir(args)
    metadata = read_runtime_metadata(data_dir / "runtime.json")
    if (args.channel is None) != (args.runtime is None):
        parser.error("--channel and --runtime must be provided together")
    if args.channel is None or args.runtime is None:
        if metadata is None:
            parser.error("restart requires --channel and --runtime when bcn is stopped")
        args.channel = metadata.channel_slug
        args.runtime = metadata.runtime_slug
        args.storage = metadata.storage_slug
        args.audit = metadata.audit_slug
        if args.endpoint is None and metadata.endpoint.startswith("unix://"):
            args.endpoint = Path(metadata.endpoint.removeprefix("unix://"))
    await _stop_daemon(args, parser)
    return await _start_daemon(args, parser)


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
