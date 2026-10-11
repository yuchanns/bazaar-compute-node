from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy from collections.abc import Sequence

import click
from bazaar_contrib_config import ConfigDocument, Configuration
from bazaar_contrib_invoke import COMMANDS, CommandCatalog, Commands
from bazaar_core import Composition, Context, Contrib, ContribEntry

lazy from .boot import build, node


async def dispatch(
    args: Sequence[str],
    *,
    prog_name: str | None,
    configer: str,
    configer_options: tuple[str, ...],
) -> None:
    failures: list[click.ClickException | click.exceptions.Exit | click.Abort] = []
    roots: list[str] = []
    selected: list[Commands] = []

    def start(document: ConfigDocument) -> Composition:
        catalog = CommandCatalog(
            contribs=(
                *(
                    entry.entry
                    for package in document.contribs
                    if package.applies
                    for entry in package.enabled
                ),
            ),
            help=main.help,
            params=main.params,
        )
        try:
            entry = catalog.select(args, prog_name=prog_name)
            if entry is not None:
                roots.append(entry.contrib)
        except (click.ClickException, click.exceptions.Exit, click.Abort) as error:
            failures.append(error)
        return build(document).model_copy(update={"roots": tuple(roots)})

    @asynccontextmanager
    async def observe(ctx: Context):
        commands = ctx.accept(COMMANDS)
        commands.help = main.help
        commands.params.extend(main.params)
        selected.append(commands)
        yield

    async with node(
        start=start, configer=configer, configer_options=configer_options
    ) as hub:
        Configuration.check(hub.validate(Composition()))
        if roots:
            async with hub.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="command-dispatch",
                            definition=Contrib(accepts=[COMMANDS], lifespan=observe),
                        )
                    ]
                )
            ):
                try:
                    await selected[0].dispatch(args, prog_name=prog_name)
                except (
                    click.ClickException,
                    click.exceptions.Exit,
                    click.Abort,
                ) as error:
                    failures.append(error)
    if failures:
        raise failures[0]


@click.command(
    add_help_option=False,
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "allow_interspersed_args": False,
    },
)
@click.version_option(package_name="bazaar-compute-node")
@click.option(
    "--configer",
    default="config-filesystem",
    show_default=True,
    help="Configuration contrib to use.",
)
@click.option(
    "--configer-options",
    multiple=True,
    metavar="OPTION",
    help="Option passed to the configuration contrib; may be repeated.",
)
@click.option(
    "--config",
    "configer_options",
    multiple=True,
    metavar="OPTION",
    help="Legacy alias for --configer-options.",
)
@click.pass_context
def main(ctx: click.Context, configer: str, configer_options: tuple[str, ...]) -> None:
    """Run Bazaar Compute Node."""
    try:
        asyncio.run(
            dispatch(
                ctx.args,
                prog_name=ctx.info_name,
                configer=configer,
                configer_options=configer_options,
            )
        )
    except click.exceptions.Exit as error:
        ctx.exit(error.exit_code)
    except click.ClickException, click.Abort:
        raise
    except Exception as error:
        pending: list[BaseException] = [error]
        errors: list[str] = []
        while pending:
            error = pending.pop()
            if isinstance(error, BaseExceptionGroup):
                pending.extend(error.exceptions)
            else:
                errors.append(str(error))
        raise click.ClickException("; ".join(errors)) from error
