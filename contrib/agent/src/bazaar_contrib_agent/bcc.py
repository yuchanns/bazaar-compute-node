"""Start the selected contrib's native bcc command in an owned composition."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import os
lazy from collections.abc import Sequence

import click
from bazaar_contrib_invoke import COMMANDS, CommandCatalog, Commands
from bazaar_core import (
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ContribSpec,
    discover,
    load,
)


async def dispatch(args: Sequence[str], *, prog_name: str | None) -> None:
    catalog = await asyncio.to_thread(
        lambda: CommandCatalog(
            contribs=tuple(source.name for source in discover()),
            entry_point_group="bazaar_compute_node.bcc_commands",
            help=main.help,
            epilog=main.epilog,
            params=main.params,
            context_settings={"help_option_names": ["-h", "--help"]},
        )
    )
    entry = await asyncio.to_thread(catalog.select, args, prog_name=prog_name)
    if entry is None:
        return
    composition = await asyncio.to_thread(
        load,
        (ContribSpec(contrib="commands"), ContribSpec(contrib=entry.contrib)),
    )
    selected: list[Commands] = []

    @asynccontextmanager
    async def observe(ctx: Context):
        group = ctx.accept(COMMANDS)
        group.help = main.help
        group.epilog = main.epilog
        group.context_settings = {
            **group.context_settings,
            "help_option_names": ["-h", "--help"],
        }
        group.params.extend(main.params)
        selected.append(group)
        yield

    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="bcc-dispatch",
                    definition=Contrib(accepts=[COMMANDS], lifespan=observe),
                ),
            ),
        }
    )
    context = {
        name: os.environ.get(variable)
        for name, variable in (
            ("agent_id", "BCN_AGENT_ID"),
            ("mode", "BCN_AGENT_MODE"),
            ("actor_id", "BCN_ACTOR_ID"),
            ("session_id", "BCN_RUNTIME_SESSION_ID"),
        )
    }
    async with ContribHub() as hub, hub.enable(composition):
        await selected[0].dispatch(args, prog_name=prog_name, obj=context)


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
