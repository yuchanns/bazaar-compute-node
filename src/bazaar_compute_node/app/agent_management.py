from __future__ import annotations

import argparse
import sys
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
                f"channel={agent.channel.kind} runtime={_runtime_kinds(agent)}",
                flush=True,
            )
        return 0

    if command == "add":
        agent_values: dict[str, object] = {}
        channel_options: dict[str, object] = {}
        runtime_values: dict[str, object] = {}
        runtime_env: dict[str, str] = {}
        seen: set[tuple[str, str]] = set()
        for scope, key, value in args.agent_options:
            # runtime.env accumulates one name per --set; a repeated name is
            # simply overwritten, the same as a hand-edited config.toml would be
            if (scope, key) == ("runtime", "env"):
                runtime_env.update(value)
                continue
            identity = (scope, key)
            if identity in seen:
                parser.error(f"duplicate --set option: {scope}.{key}")
            seen.add(identity)
            if scope == "agent":
                agent_values[key] = value
            elif scope == "channel":
                channel_options[key] = value
            else:
                runtime_values[key] = value

        idle_timeout = agent_values.pop("idle_timeout", 0)
        if isinstance(idle_timeout, bool) or not isinstance(idle_timeout, int | float):
            parser.error("agent.idle_timeout must be a number")

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
                runtimes=(
                    RuntimeConfiguration(
                        kind=args.runtime,
                        model=model,
                        effort=effort,
                        sandbox_mode=sandbox_mode,
                        network_access=network_access,
                        env=MappingProxyType(runtime_env),
                        options=MappingProxyType(runtime_values),
                    ),
                ),
                idle_timeout_seconds=idle_timeout,
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
            f"channel={agent.channel.kind} runtime={_runtime_kinds(agent)}",
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
    if not dot or scope not in {"agent", "channel", "runtime"} or not key:
        raise argparse.ArgumentTypeError(
            "--set path must start with agent., channel. or runtime."
        )
    if key != key.strip():
        raise argparse.ArgumentTypeError(
            "--set option key cannot contain edge whitespace"
        )
    if key == "kind" and scope != "agent":
        raise argparse.ArgumentTypeError(
            f"{scope}.kind must be provided with --{scope}"
        )
    if scope == "runtime" and key == "env":
        return scope, key, _env_pair(raw_value)
    try:
        parsed = tomllib.loads(f"value = {raw_value}")["value"]
    except tomllib.TOMLDecodeError:
        parsed = raw_value
    if scope == "runtime" and key == "env_include":
        return scope, "env", _env_from_deprecated_include(parsed)
    return scope, key, parsed


def _env_pair(value: str) -> dict[str, str]:
    name, separator, source = value.partition("=")
    if not separator or not name or not source:
        raise argparse.ArgumentTypeError(
            "runtime.env must use <name>=<source>, for example "
            "--set runtime.env=CODEX_HOME=BCN_CODEX_HOME_WORK"
        )
    return {name: source}


def _env_from_deprecated_include(value: object) -> dict[str, str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise argparse.ArgumentTypeError(
            "runtime.env_include must be an array of non-empty text"
        )
    if len(set(value)) != len(value):
        raise argparse.ArgumentTypeError(
            "runtime.env_include cannot contain duplicates"
        )
    print(
        create_translator(None).text("cli.agent.env_include_deprecated"),
        file=sys.stderr,
        flush=True,
    )
    return {name: name for name in value}


def _runtime_kinds(agent: AgentConfiguration) -> str:
    return ",".join(runtime.kind for runtime in agent.runtimes)


__all__ = ["build_agent_parser", "run_agent_command"]
