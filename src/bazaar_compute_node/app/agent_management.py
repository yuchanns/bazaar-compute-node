from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid7

from ..core.runtime import RuntimeSandboxMode
from ..i18n import create_translator
from .config import (
    AgentConfiguration,
    ChannelConfiguration,
    ConfigurationError,
    NodeConfiguration,
    RuntimeConfiguration,
    _write_configuration,
    load_node_configuration,
    resolve_config_path,
)
from .usage import Usage


@dataclass(frozen=True, slots=True)
class _SetOptions:
    agent: dict[str, object]
    channel: dict[str, object]
    runtime: dict[str, object]
    env: dict[str, str]


def _set_options(
    agent_options: Sequence[tuple[str, str, Any]], parser: Usage
) -> _SetOptions:
    """Sort every --set value under the scope it names."""

    options = _SetOptions(agent={}, channel={}, runtime={}, env={})
    seen: set[tuple[str, str]] = set()
    for scope, key, value in agent_options:
        # runtime.env accumulates one name per --set; a repeated name is
        # simply overwritten, the same as a hand-edited config.toml would be
        if (scope, key) == ("runtime", "env"):
            options.env.update(value)
            continue
        identity = (scope, key)
        if identity in seen:
            parser.error(f"duplicate --set option: {scope}.{key}")
        seen.add(identity)
        if scope == "agent":
            options.agent[key] = value
        elif scope == "channel":
            options.channel[key] = value
        else:
            options.runtime[key] = value
    return options


def _runtime_configuration(
    kind: str, options: _SetOptions, parser: Usage
) -> RuntimeConfiguration:
    """Read the runtime settings out of what --set collected."""

    values = options.runtime
    model = values.pop("model", None)
    effort = values.pop("effort", None)
    raw_sandbox_mode = values.pop(
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
    network_access = values.pop("network_access", True)
    if not isinstance(network_access, bool):
        parser.error("runtime.network_access must be a boolean")
    if model is not None and not isinstance(model, str):
        parser.error("runtime.model must be text")
    if effort is not None and not isinstance(effort, str):
        parser.error("runtime.effort must be text")
    return RuntimeConfiguration(
        kind=kind,
        model=model,
        effort=effort,
        sandbox_mode=sandbox_mode,
        network_access=network_access,
        env=MappingProxyType(options.env),
        options=MappingProxyType(values),
    )


def _list_agents(configuration: NodeConfiguration) -> int:
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


def _add_agent(
    args: argparse.Namespace,
    parser: Usage,
    configuration: NodeConfiguration,
    config_path: Path,
) -> int:
    options = _set_options(args.agent_options, parser)

    idle_timeout = options.agent.pop("idle_timeout", 0)
    if isinstance(idle_timeout, bool) or not isinstance(idle_timeout, int | float):
        parser.error("agent.idle_timeout must be a number")

    try:
        agent = AgentConfiguration(
            id=str(uuid7()),
            name=args.name,
            channel=ChannelConfiguration(
                kind=args.channel,
                options=MappingProxyType(options.channel),
            ),
            runtimes=(_runtime_configuration(args.runtime, options, parser),),
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


def _remove_agent(
    args: argparse.Namespace,
    parser: Usage,
    configuration: NodeConfiguration,
    config_path: Path,
) -> int:
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
        agents=tuple(agent for agent in configuration.agents if agent.id != removed.id),
    )
    try:
        _write_configuration(config_path, updated)
    except ConfigurationError as error:
        parser.error(str(error))
    print(f"Agent removed id={removed.id} name={removed.name}", flush=True)
    print("Workspace and durable data were preserved.", flush=True)
    print("Run `bcn restart` to apply.", flush=True)
    return 0


def run_agent_command(
    args: argparse.Namespace,
    parser: Usage,
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

    match args.agent_command:
        case "list":
            return _list_agents(configuration)
        case "add":
            return _add_agent(args, parser, configuration, config_path)
        case "remove":
            return _remove_agent(args, parser, configuration, config_path)
        case unsupported:
            raise AssertionError(f"unsupported Agent command: {unsupported}")


def _runtime_kinds(agent: AgentConfiguration) -> str:
    return ",".join(runtime.kind for runtime in agent.runtimes)


__all__ = ["run_agent_command"]
