"""Run the Agent's fixed native commands through the configured sidecar."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy from collections.abc import Mapping, Sequence
lazy from pathlib import Path
lazy from types import SimpleNamespace

import click
from bazaar_contrib_invoke import Commands
lazy from bazaar_contrib_messages import Actor, Actors, Agent, Mode

lazy from .cli.messages.inbox import inbox
lazy from .cli.messages.message import message
lazy from .cli.messages.thread import thread
lazy from .cli.reminder.reminder import reminder
lazy from .tool import request


async def dispatch(args: Sequence[str], *, prog_name: str | None) -> None:
    commands = Commands(
        help=main.help,
        epilog=main.epilog,
        params=main.params,
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    for command in (message, thread, reminder):
        commands.add_command(command)
    actor: Actor | None = None
    agent_id = os.environ.get("BCN_AGENT_ID")
    mode = os.environ.get("BCN_AGENT_MODE")
    if agent_id is not None and mode is not None:
        actors = Actors(agent_id, Mode(mode))
        if actor_id := os.environ.get("BCN_ACTOR_ID"):
            actor = actors.resolve(actor_id)
        elif actors.mode is Mode.DANGEROUS_INDIVIDUAL:
            # The standalone launcher knows its Agent before a session is bound.
            actor = Agent(agent_id)
    if isinstance(actor, Agent):
        commands.add_command(inbox)

    async def send(service: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        path = os.environ.get("BCN_TOOL_CONFIG")
        if not path:
            return {
                "ok": False,
                "code": "TOOL_CONFIG_REQUIRED",
                "error": "BCN_TOOL_CONFIG is not set",
            }
        try:
            return await request(Path(path), service, payload)
        except Exception as error:  # noqa: BLE001 - Preserve the command error envelope for the CLI.
            return {
                "ok": False,
                "code": "COMMAND_FAILED",
                "error": str(error) or type(error).__name__,
            }

    await commands.dispatch(
        args,
        prog_name=prog_name,
        obj=SimpleNamespace(request=send),
    )


@click.command(
    add_help_option=False,
    help=(
        "Session-scoped collaboration commands for a Bazaar Compute Node. "
        "Use these commands from the current agent session to inspect messages, "
        "send replies, manage thread attention, and schedule persistent reminders."
    ),
    epilog=(
        "Run `bcc <resource> --help` or "
        "`bcc <resource> <command> --help` for command-specific usage."
    ),
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "allow_interspersed_args": False,
    },
)
@click.pass_context
def main(ctx: click.Context) -> None:
    try:
        asyncio.run(dispatch(ctx.args, prog_name=ctx.info_name))
    except click.exceptions.Exit as error:
        ctx.exit(error.exit_code)
    except click.ClickException:
        raise
    except KeyboardInterrupt:
        pass
    except Exception as error:
        raise click.ClickException(str(error)) from error


if __name__ == "__main__":
    main(prog_name="bcc")
