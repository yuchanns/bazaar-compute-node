from __future__ import annotations

import click

from ._client import request, run
from ._format import echo, serialize_unfollow


@click.group(help="Thread attention operations")
def thread() -> None: ...


@thread.command(
    short_help="Stop following a group/thread target.",
    help=(
        "Stop following the current group/thread target for future message wakes. "
        "This does not affect Reminder ownership or Reminder wakes."
    ),
)
@click.option(
    "--target",
    required=True,
    metavar="<target>",
    help="Group/thread target to unfollow.",
)
@run
async def unfollow(target: str) -> None:
    echo(serialize_unfollow(await request("thread", "unfollow", {"target": target})))


__all__ = ["thread"]
