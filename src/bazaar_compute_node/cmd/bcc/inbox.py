from __future__ import annotations

import click

from ._client import request, run
from ._format import echo, serialize_inbox_list


@click.group(help="Inbox discovery operations")
def inbox() -> None: ...


@inbox.command("list", help="List available message targets")
@click.option(
    "--limit",
    type=int,
    default=100,
    metavar="<n>",
    help="Maximum number of targets to return (default: 100).",
)
@click.option(
    "--offset",
    type=int,
    default=0,
    metavar="<n>",
    help="Number of targets to skip (default: 0).",
)
@run
async def list_targets(limit: int, offset: int) -> None:
    echo(
        serialize_inbox_list(
            await request("inbox", "list", {"limit": limit, "offset": offset})
        )
    )


__all__ = ["inbox"]
