from __future__ import annotations

import argparse
import asyncio
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
    load_node_configuration,
    resolve_config_path,
)
from .app.registry import AdapterRegistry, ProviderLoadError, SharedAdapterFactories
from .app.system_service import (
    build_system_service_parser,
    run_system_service_command,
)
from .core.paths import resolve_data_dir
from .i18n import Translator, create_translator


def build_parser(translator: Translator | None = None) -> argparse.ArgumentParser:
    translator = translator or create_translator(None)
    default_data_dir = resolve_data_dir()
    parser = argparse.ArgumentParser(
        prog="bcn",
        description=translator.text(
            "cli.bcn.description",
            {"data_dir": default_data_dir},
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
        choices=("start", "stop", "restart", "run", "agent", "system-service"),
        help=translator.text("cli.bcn.command"),
    )
    parser.add_argument("--storage")
    parser.add_argument("--audit")
    parser.add_argument(
        "--config",
        type=Path,
        help=translator.text("cli.bcn.config"),
    )
    parser.add_argument(
        "--database-name",
        type=_database_name,
        help=translator.text("cli.bcn.database_name"),
    )
    parser.add_argument(
        "--endpoint",
        type=Path,
        help=translator.text("cli.bcn.endpoint"),
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help=translator.text("cli.bcn.foreground"),
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


def _show_nested_help_if_requested(
    argv: Sequence[str] | None,
    translator: Translator,
) -> None:
    raw_arguments = tuple(sys.argv[1:] if argv is None else argv)
    for command, builder in (
        ("agent", build_agent_parser),
        ("system-service", build_system_service_parser),
    ):
        if command not in raw_arguments:
            continue
        command_index = raw_arguments.index(command)
        nested_arguments = raw_arguments[command_index + 1 :]
        if "--help" in nested_arguments or "-h" in nested_arguments:
            builder(translator).parse_args(nested_arguments)
        return


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


def _format_runtimes(value: object) -> str:
    if isinstance(value, Sequence) and not isinstance(value, str):
        return ",".join(str(item) for item in value)
    return str(value)


def _print_agent_startup_records(records: Sequence[Mapping[str, object]]) -> None:
    for record in records:
        line = (
            f"agent startup id={record.get('agent_id')} name={record.get('name')} "
            f"status={record.get('status')} channel={record.get('channel')} "
            f"runtime={_format_runtimes(record.get('runtimes'))}"
        )
        error_type = record.get("error_type")
        error = record.get("error")
        if isinstance(error_type, str) and error_type:
            line += f" error_type={error_type}"
        if isinstance(error, str) and error:
            line += f" error={error}"
        print(line, flush=True)


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
    if command == "system-service":
        return await run_system_service_command(args, parser)
    if command in {"start", "stop", "restart"}:
        print(
            create_translator(None).text(f"cli.bcn.deprecation.{command}"),
            file=sys.stderr,
            flush=True,
        )
        args.system_service_command = command
        return await run_system_service_command(
            args,
            build_system_service_parser(),
        )
    if command == "run":
        return await _run_node(args, parser)
    parser.error(f"unsupported command: {command}")


def _prepare_cli_arguments(
    argv: Sequence[str] | None,
) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    translator = create_translator(None)
    _show_nested_help_if_requested(argv, translator)
    parser = build_parser(translator)
    args, remaining = parser.parse_known_args(argv)
    if args.command == "agent":
        agent_args = build_agent_parser(translator).parse_args(remaining)
        vars(args).update(vars(agent_args))
    elif args.command == "system-service":
        system_service_args = build_system_service_parser(translator).parse_args(
            remaining
        )
        vars(args).update(vars(system_service_args))
    elif remaining:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")

    if args.config is not None:
        args.config = args.config.expanduser().resolve()

    if args.command == "agent":
        return parser, args
    if args.command == "system-service":
        return parser, args
    if args.command == "start":
        return parser, args
    if args.command in {"start", "stop", "restart"}:
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
