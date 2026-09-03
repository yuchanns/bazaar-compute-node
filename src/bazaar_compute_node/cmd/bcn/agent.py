from __future__ import annotations

import tomllib

import click

from ...app.agent_management import run_agent_command
from ...i18n import Translator, create_translator
from ._options import inherited, node_options, remember
from ._runner import UsageReporter, arguments


def _agent_option(value: str) -> tuple[str, str, object]:
    path, separator, raw_value = value.partition("=")
    if not separator:
        raise click.BadParameter("--set must use <scope.key=value>")
    if path != path.strip():
        raise click.BadParameter("--set option path cannot contain whitespace")
    scope, dot, key = path.partition(".")
    if not dot or scope not in {"agent", "channel", "runtime"} or not key:
        raise click.BadParameter(
            "--set path must start with agent., channel. or runtime."
        )
    if key != key.strip():
        raise click.BadParameter("--set option key cannot contain edge whitespace")
    if key == "kind" and scope != "agent":
        raise click.BadParameter(f"{scope}.kind must be provided with --{scope}")
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
        raise click.BadParameter(
            "runtime.env must use <name>=<source>, for example "
            "--set runtime.env=CODEX_HOME=BCN_CODEX_HOME_WORK"
        )
    return {name: source}


def _env_from_deprecated_include(value: object) -> dict[str, str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise click.BadParameter(
            "runtime.env_include must be an array of non-empty text"
        )
    if len(set(value)) != len(value):
        raise click.BadParameter("runtime.env_include cannot contain duplicates")
    click.echo(
        create_translator(None).text("cli.agent.env_include_deprecated"),
        err=True,
    )
    return {name: name for name in value}


def build_agent_group(translator: Translator) -> click.Group:
    @click.group(
        "agent",
        help=translator.text("cli.agent.description"),
    )
    @node_options(translator)
    @click.pass_context
    def agent(context: click.Context, **values: object) -> None:
        remember(context, **values)

    def run(agent_command: str, **values: object) -> int:
        return run_agent_command(
            arguments(agent_command=agent_command, **values),
            UsageReporter(),
        )

    @agent.command(
        "list",
        help=translator.text("cli.agent.list"),
        short_help=translator.text("cli.agent.list"),
    )
    @node_options(translator)
    def list_agents(**values: object) -> None:
        raise SystemExit(run("list", **inherited(**values)))

    @agent.command(
        "add",
        help=translator.text("cli.agent.add"),
        short_help=translator.text("cli.agent.add"),
    )
    @click.option("--name", required=True, help=translator.text("cli.agent.name"))
    @click.option("--channel", required=True, help=translator.text("cli.agent.channel"))
    @click.option("--runtime", required=True, help=translator.text("cli.agent.runtime"))
    @click.option(
        "--set",
        "agent_options",
        multiple=True,
        metavar="<scope.key=value>",
        callback=lambda *call: [_agent_option(value) for value in call[-1]],
        help=translator.text("cli.agent.set"),
    )
    @node_options(translator)
    def add(**values: object) -> None:
        raise SystemExit(run("add", **inherited(**values)))

    @agent.command(
        "remove",
        help=translator.text("cli.agent.remove"),
        short_help=translator.text("cli.agent.remove"),
    )
    @click.argument("selector")
    @node_options(translator)
    def remove(**values: object) -> None:
        raise SystemExit(run("remove", **inherited(**values)))

    # the decorators registered these; naming them again keeps that visible
    del list_agents, add, remove
    return agent


__all__ = ["build_agent_group"]
