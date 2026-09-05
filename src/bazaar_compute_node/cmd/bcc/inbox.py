from __future__ import annotations

import click

from ._client import request, run
from ._format import echo, serialize_inbox_check


@click.group(help="Inbox target summary operations")
def inbox() -> None: ...


@inbox.command(
    "check",
    help="Show pending inbox targets without draining or reading message content.",
)
@run
async def check_targets() -> None:
    echo(serialize_inbox_check(await request("inbox", "check", {})))


__all__ = ["inbox"]
