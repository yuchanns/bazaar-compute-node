from __future__ import annotations

import argparse
import tomllib
from dataclasses import replace
from types import MappingProxyType
from uuid import uuid7

from ..core.runtime import RuntimeSandboxMode
from ..i18n import Translator, create_translator
from .config import (
    AgentConfiguration,
    ChannelConfiguration,
    ConfigurationError,
    RuntimeConfiguration,
    _write_configuration,
    load_node_configuration,
    resolve_config_path,
)


def build_agent_parser(
    translator: Translator | None = None,
) -> argparse.ArgumentParser:
    translator = translator or create_translator(None)
    parser = argparse.ArgumentParser(
        prog="bcn agent",
        description=translator.text("cli.agent.description"),
    )
    commands = parser.add_subparsers(dest="agent_command", required=True)
    commands.add_parser("list", help=translator.text("cli.agent.list"))

    add = commands.add_parser("add", help=translator.text("cli.agent.add"))
    add.add_argument("--name", required=True, help=translator.text("cli.agent.name"))
    add.add_argument(
        "--channel",
        required=True,
        help=translator.text("cli.agent.channel"),
    )
    add.add_argument(
        "--runtime",
        required=True,
        help=translator.text("cli.agent.runtime"),
    )
    add.add_argument(
        "--set",
        action="append",
        default=[],
        dest="agent_options",
        metavar="<scope.key=value>",
        type=_agent_option,
        help=translator.text("cli.agent.set"),
    )

    remove = commands.add_parser("remove", help=translator.text("cli.agent.remove"))
    remove.add_argument("selector", help=translator.text("cli.agent.selector"))
    return parser


def run_agent_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    if (
        args.storage is not None
        or args.audit is not None
        or args.database_name is not None
        or args.endpoint is not None
        or args.foreground
    ):
        parser.error("bcn agent commands only accept the node-level --config option")

    config_path = (args.config or resolve_config_path()).expanduser()
    try:
        configuration = load_node_configuration(config_path)
    except ConfigurationError as error:
        parser.error(str(error))

    command = args.agent_command
    if command == "list":
        if not configuration.agents:
            print(create_translator(None).text("cli.agent.empty"), flush=True)
            return 0
        for agent in configuration.agents:
            print(
                f"id={agent.id} name={agent.name} "
                f"channel={agent.channel.kind} runtime={agent.runtime.kind}",
                flush=True,
            )
        return 0

    if command == "add":
        channel_options: dict[str, object] = {}
        runtime_values: dict[str, object] = {}
        seen: set[tuple[str, str]] = set()
        for scope, key, value in args.agent_options:
            identity = (scope, key)
            if identity in seen:
                parser.error(f"duplicate --set option: {scope}.{key}")
            seen.add(identity)
            if scope == "channel":
                channel_options[key] = value
            else:
                runtime_values[key] = value

        model = runtime_values.pop("model", None)
        effort = runtime_values.pop("effort", None)
        raw_sandbox_mode = runtime_values.pop(
            "sandbox_mode",
            RuntimeSandboxMode.WORKSPACE_WRITE.value,
        )
        if not isinstance(raw_sandbox_mode, str):
            parser.error("runtime.sandbox_mode must be text")
        try:
            sandbox_mode = RuntimeSandboxMode(raw_sandbox_mode)
        except ValueError:
            allowed = ", ".join(mode.value for mode in RuntimeSandboxMode)
            parser.error(f"runtime.sandbox_mode must be one of: {allowed}")
        network_access = runtime_values.pop("network_access", True)
        if not isinstance(network_access, bool):
            parser.error("runtime.network_access must be a boolean")
        idle_timeout = runtime_values.pop("idle_timeout", 0)
        if isinstance(idle_timeout, bool) or not isinstance(idle_timeout, int | float):
            parser.error("runtime.idle_timeout must be a number")
        raw_env_include = runtime_values.pop("env_include", [])
        if not isinstance(raw_env_include, list) or any(
            not isinstance(item, str) or not item for item in raw_env_include
        ):
            parser.error("runtime.env_include must be an array of non-empty text")
        if len(set(raw_env_include)) != len(raw_env_include):
            parser.error("runtime.env_include cannot contain duplicates")
        if model is not None and not isinstance(model, str):
            parser.error("runtime.model must be text")
        if effort is not None and not isinstance(effort, str):
            parser.error("runtime.effort must be text")

        try:
            agent = AgentConfiguration(
                id=str(uuid7()),
                name=args.name,
                channel=ChannelConfiguration(
                    kind=args.channel,
                    options=MappingProxyType(channel_options),
                ),
                runtime=RuntimeConfiguration(
                    kind=args.runtime,
                    model=model,
                    effort=effort,
                    sandbox_mode=sandbox_mode,
                    network_access=network_access,
                    idle_timeout_seconds=idle_timeout,
                    env_include=tuple(raw_env_include),
                    options=MappingProxyType(runtime_values),
                ),
            )
            updated = replace(
                configuration,
                agents=(*configuration.agents, agent),
            )
            _write_configuration(config_path, updated)
        except ConfigurationError as error:
            parser.error(str(error))
        print(
            f"Agent added id={agent.id} name={agent.name} "
            f"channel={agent.channel.kind} runtime={agent.runtime.kind}",
            flush=True,
        )
        print("Run `bcn restart` to apply.", flush=True)
        return 0

    if command == "remove":
        matches = [
            agent
            for agent in configuration.agents
            if agent.id == args.selector or agent.name == args.selector
        ]
        if not matches:
            parser.error(f"Agent not found: {args.selector}")
        if len(matches) > 1:
            parser.error(f"Agent selector is ambiguous: {args.selector}")
        removed = matches[0]
        updated = replace(
            configuration,
            agents=tuple(
                agent for agent in configuration.agents if agent.id != removed.id
            ),
        )
        try:
            _write_configuration(config_path, updated)
        except ConfigurationError as error:
            parser.error(str(error))
        print(f"Agent removed id={removed.id} name={removed.name}", flush=True)
        print("Workspace and durable data were preserved.", flush=True)
        print("Run `bcn restart` to apply.", flush=True)
        return 0

    raise AssertionError(f"unsupported Agent command: {command}")


def _agent_option(value: str) -> tuple[str, str, object]:
    path, separator, raw_value = value.partition("=")
    if not separator:
        raise argparse.ArgumentTypeError("--set must use <scope.key=value>")
    if path != path.strip():
        raise argparse.ArgumentTypeError("--set option path cannot contain whitespace")
    scope, dot, key = path.partition(".")
    if not dot or scope not in {"channel", "runtime"} or not key:
        raise argparse.ArgumentTypeError(
            "--set path must start with channel. or runtime."
        )
    if key != key.strip():
        raise argparse.ArgumentTypeError(
            "--set option key cannot contain edge whitespace"
        )
    if key == "kind":
        raise argparse.ArgumentTypeError(
            f"{scope}.kind must be provided with --{scope}"
        )
    try:
        parsed = tomllib.loads(f"value = {raw_value}")["value"]
    except tomllib.TOMLDecodeError:
        parsed = raw_value
    return scope, key, parsed


__all__ = ["build_agent_parser", "run_agent_command"]
