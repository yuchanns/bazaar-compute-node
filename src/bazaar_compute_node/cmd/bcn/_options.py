from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from ...i18n import Translator


def _database_name(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise click.BadParameter(
            "database name must be a single non-empty path component"
        )
    return value


NODE_SETTINGS = (
    "storage",
    "audit",
    "config",
    "database_name",
    "endpoint",
    "foreground",
)


def remember(context: click.Context, **values: object) -> None:
    """Keep the settings this level was given, over whatever came above it."""

    inherited_so_far = context.obj if isinstance(context.obj, dict) else {}
    context.obj = {
        **inherited_so_far,
        **{name: value for name, value in values.items() if value not in (None, False)},
    }


def inherited(**values: object) -> dict[str, object]:
    """Let a setting given before the subcommand stand in for an omitted one."""

    context = click.get_current_context()
    defaults = context.obj if isinstance(context.obj, dict) else {}
    settings = {
        name: values.get(name)
        if values.get(name) not in (None, False)
        else defaults.get(name)
        for name in NODE_SETTINGS
    }
    return {**values, **settings}


def node_options[**P](
    translator: Translator,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
    """Attach the settings every bcn command reads before it does anything."""

    def decorate(command: Callable[P, Any]) -> Callable[P, Any]:
        for option in reversed(
            (
                click.option("--storage"),
                click.option("--audit"),
                click.option(
                    "--config",
                    type=click.Path(path_type=Path),
                    help=translator.text("cli.bcn.config"),
                ),
                click.option(
                    "--database-name",
                    callback=lambda *call: (
                        _database_name(call[-1]) if call[-1] is not None else None
                    ),
                    help=translator.text("cli.bcn.database_name"),
                ),
                click.option(
                    "--endpoint",
                    type=click.Path(path_type=Path),
                    help=translator.text("cli.bcn.endpoint"),
                ),
                click.option(
                    "--foreground",
                    is_flag=True,
                    help=translator.text("cli.bcn.foreground"),
                ),
            )
        ):
            command = option(command)
        return command

    return decorate


__all__ = ["NODE_SETTINGS", "inherited", "node_options", "remember"]
