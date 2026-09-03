from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import click

from ...app.application import NodeApplication
from ...app.config import (
    DEFAULT_AUDIT,
    DEFAULT_STORAGE,
    ConfigurationError,
    NodeConfiguration,
    load_node_configuration,
)
from ...app.registry import AdapterRegistry, ProviderLoadError, SharedAdapterFactories
from ...app.system_service import run_system_service_command
from ...app.usage import Usage
from ...core.paths import resolve_data_dir
from ...i18n import Translator, create_translator
from ._options import inherited, node_options
from ._runner import UsageReporter, arguments


def _endpoint_path(args: argparse.Namespace, data_dir: Path) -> Path:
    return (args.endpoint or data_dir / "bcn.sock").expanduser()


def _apply_runtime_configuration(
    args: argparse.Namespace,
    parser: Usage,
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
    parser: Usage,
    args: argparse.Namespace,
) -> NodeConfiguration:
    configuration = getattr(args, "configuration", None)
    if not isinstance(configuration, NodeConfiguration):
        parser.error("startup configuration has not been loaded")
    return configuration


def _load_shared_factories(
    args: argparse.Namespace,
    parser: Usage,
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
        runtimes = record.get("runtimes")
        if isinstance(runtimes, Sequence) and not isinstance(runtimes, str):
            runtimes = ",".join(str(item) for item in runtimes)
        line = (
            f"agent startup id={record.get('agent_id')} name={record.get('name')} "
            f"status={record.get('status')} channel={record.get('channel')} "
            f"runtime={runtimes}"
        )
        error_type = record.get("error_type")
        error = record.get("error")
        if isinstance(error_type, str) and error_type:
            line += f" error_type={error_type}"
        if isinstance(error, str) and error:
            line += f" error={error}"
        print(line, flush=True)


async def _run_node(args: argparse.Namespace, parser: Usage) -> int:
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
    return node.exit_code


def build_node_commands(translator: Translator) -> tuple[click.Command, ...]:
    @click.command(
        "run",
        help=translator.text("cli.bcn.run"),
        short_help=translator.text("cli.bcn.run"),
    )
    @node_options(translator)
    def run(**values: object) -> None:
        # a first install has no configuration until run writes one
        args = arguments(**inherited(**values))
        _apply_runtime_configuration(args, UsageReporter())
        if args.config is not None:
            args.config = args.config.expanduser().resolve()
        raise SystemExit(asyncio.run(_run_node(args, UsageReporter())))

    commands: list[click.Command] = [run]
    for name in ("start", "stop", "restart"):

        @click.command(
            name,
            help=translator.text(f"cli.bcn.{name}"),
            short_help=translator.text(f"cli.bcn.{name}"),
        )
        @node_options(translator)
        def deprecated(_name: str = name, **values: object) -> None:
            # these names now belong to the service manager, and the node only
            # forwards them so an old habit still lands somewhere sensible
            click.echo(
                create_translator(None).text(f"cli.bcn.deprecation.{_name}"),
                err=True,
            )
            raise SystemExit(
                asyncio.run(
                    run_system_service_command(
                        arguments(system_service_command=_name, **inherited(**values)),
                        UsageReporter(),
                    )
                )
            )

        commands.append(deprecated)
    return tuple(commands)


__all__ = ["build_node_commands"]
