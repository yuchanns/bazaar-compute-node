from __future__ import annotations

import click

from ._client import MessageClient, run
from ._format import echo, serialize_inbox_check


@click.group(help="Inbox target summary operations")
@click.pass_context
def inbox(ctx: click.Context) -> None:
    ctx.obj = MessageClient(ctx.obj.request)


@inbox.command(
    "check",
    help="Show pending inbox targets without draining or reading message content.",
)
@click.pass_obj
@run
async def check_targets(client: MessageClient) -> None:
    echo(serialize_inbox_check(await client.request("inbox", "check", {})))


__all__ = ["inbox"]
