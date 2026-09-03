from __future__ import annotations

import asyncio
from pathlib import Path

import click

from ...app.system_service import run_system_service_command
from ...i18n import Translator
from ._options import inherited, node_options, remember
from ._runner import UsageReporter, arguments


def build_service_group(translator: Translator) -> click.Group:
    @click.group(
        "system-service",
        help=translator.text("cli.system_service.description"),
    )
    @node_options(translator)
    @click.pass_context
    def service(context: click.Context, **values: object) -> None:
        remember(context, **values)

    def run(system_service_command: str, **values: object) -> int:
        return asyncio.run(
            run_system_service_command(
                arguments(system_service_command=system_service_command, **values),
                UsageReporter(),
            )
        )

    @service.command(
        "install",
        help=translator.text("cli.system_service.install"),
        short_help=translator.text("cli.system_service.install"),
    )
    @click.option(
        "--env-file",
        type=click.Path(path_type=Path),
        help=translator.text("cli.system_service.env_file"),
    )
    @node_options(translator)
    def install(**values: object) -> None:
        raise SystemExit(run("install", **inherited(**values)))

    for name in ("start", "stop", "restart", "uninstall", "status"):

        @service.command(
            name,
            help=translator.text(f"cli.system_service.{name}"),
            short_help=translator.text(f"cli.system_service.{name}"),
        )
        @node_options(translator)
        def command(_name: str = name, **values: object) -> None:
            raise SystemExit(run(_name, **inherited(**values)))

        del command
    # the decorators registered these; naming them again keeps that visible
    del install
    return service


__all__ = ["build_service_group"]
