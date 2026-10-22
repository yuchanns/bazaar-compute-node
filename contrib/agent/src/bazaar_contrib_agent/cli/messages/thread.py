from __future__ import annotations

import click

from ._client import MessageClient, run
from ._format import echo, serialize_unfollow


@click.group(help="Thread attention operations")
@click.pass_context
def thread(ctx: click.Context) -> None:
    ctx.obj = MessageClient(ctx.obj.request)


@thread.command(
    short_help=("Stop following a thread you no longer need ordinary delivery for"),
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
@click.pass_obj
@run
async def unfollow(client: MessageClient, target: str) -> None:
    echo(
        serialize_unfollow(
            await client.request("thread", "unfollow", {"target": target})
        )
    )


__all__ = ["thread"]
