from __future__ import annotations

import click

from ._client import request, run
from ._format import echo, serialize_upgrade, serialize_version


@click.group(help="Operations on the node this session runs on")
def node() -> None: ...


@node.command(
    short_help="Install the newer bcn release and restart this node",
    help=(
        "Install the newer bcn release announced in the inbox notice, schedule a "
        "reminder so you can report the outcome once the node is back, and "
        "restart the node. Run it only after the user agrees to upgrade."
    ),
)
@click.option(
    "--message-id",
    metavar="<id>",
    help=(
        "Required full uuid for the local inbound message the follow-up reminder "
        "anchors to."
    ),
)
@run
async def upgrade(message_id: str | None) -> None:
    # the node installs the release before it answers, and neither side
    # gives up on work the other would carry on doing
    echo(
        serialize_upgrade(
            await request("node", "upgrade", {"message_id": message_id}, timeout=None)
        )
    )


@node.command(
    short_help="Report the version this node is running",
    help=(
        "Report the bcn version of the running node process, which is what "
        "an upgrade has to change to have taken effect."
    ),
)
@run
async def version() -> None:
    echo(serialize_version(await request("node", "version")))


__all__ = ["node"]
