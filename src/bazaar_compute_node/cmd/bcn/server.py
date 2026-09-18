from __future__ import annotations

from pathlib import Path

import click

from ...app.server_management import run_server_command
from ...i18n import Translator
from ._options import inherited, node_options, remember
from ._runner import UsageReporter, arguments


def build_server_group(translator: Translator) -> click.Group:
    @click.group(
        "server",
        help=translator.text("cli.server.description"),
    )
    @node_options(translator)
    @click.pass_context
    def server(context: click.Context, **values: object) -> None:
        remember(context, **values)

    @server.command(
        "connect",
        help=translator.text("cli.server.connect"),
        short_help=translator.text("cli.server.connect"),
    )
    @click.option("--url", required=True, help=translator.text("cli.server.url"))
    @click.option("--token", required=True, help=translator.text("cli.server.token"))
    @click.option(
        "--env-file",
        type=click.Path(path_type=Path),
        help=translator.text("cli.server.env_file"),
    )
    @node_options(translator)
    def connect(**values: object) -> None:
        raise SystemExit(
            run_server_command(
                arguments(server_command="connect", **inherited(**values)),
                UsageReporter(),
            )
        )

    del connect
    return server


__all__ = ["build_server_group"]
