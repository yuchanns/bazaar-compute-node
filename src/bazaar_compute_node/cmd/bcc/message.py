from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid7

import click

from ._client import BccCommandError, request, run
from ._format import echo, serialize_check, serialize_read, serialize_send


@click.group(help="Message operations")
def message() -> None: ...


@message.command(
    short_help=(
        "Drain the agent inbox (non-blocking). Acks delivered seqs before returning."
    ),
    help="Drain the agent inbox (non-blocking). Acks delivered seqs before returning.",
)
@run
async def check() -> None:
    echo(serialize_check(await request("message", "check")))


@message.command(help="Read message history for a channel, DM, or thread")
@click.option(
    "--target",
    required=True,
    metavar="<target>",
    help="DM/thread target to read, as shown by `bcc message check`.",
)
@click.option(
    "--around",
    metavar="<message-id>",
    help="Center the history window around this local message id.",
)
@click.option(
    "--limit",
    type=int,
    default=100,
    metavar="<n>",
    help="Maximum number of history messages to return (default: 100).",
)
@run
async def read(target: str, around: str | None, limit: int) -> None:
    echo(
        serialize_read(
            await request(
                "message",
                "read",
                {
                    "target": target,
                    "around_message_id": around,
                    "limit": limit,
                },
            )
        )
    )


@message.command(
    short_help="Send a reply after the session fresh-check gate.",
    help=(
        "Send a message through the current Channel. The message body is read "
        "from stdin. A recent `bcc message check` or `bcc message read` snapshot "
        "is required before delivery."
    ),
)
@click.option(
    "--target", required=True, metavar="<target>", help="DM/thread target to reply to."
)
@click.option(
    "--reply-to",
    metavar="<message-id>",
    help="Optional local message id to reply to within the target.",
)
@click.option(
    "--attachment",
    multiple=True,
    metavar="<path>",
    help="Workspace file to attach; repeat the option for multiple files.",
)
@click.option(
    "--send-draft",
    is_flag=True,
    help="Send this target's active draft unchanged after rechecking freshness.",
)
@run
async def send(
    target: str,
    reply_to: str | None,
    attachment: tuple[str, ...],
    send_draft: bool,
) -> None:
    if send_draft and (reply_to is not None or attachment):
        raise BccCommandError(
            "--send-draft cannot be combined with --reply-to or --attachment",
            code="INVALID_SEND_DRAFT",
        )
    body = "" if send_draft else await asyncio.to_thread(sys.stdin.read)
    echo(
        serialize_send(
            await request(
                "message",
                "send",
                {
                    "target": target,
                    "body": body,
                    "command_id": f"bcc-{uuid7().hex}",
                    "reply_to_message_id": reply_to,
                    "send_draft": send_draft,
                    "attachment_paths": await asyncio.to_thread(
                        lambda: [str(Path(path).absolute()) for path in attachment]
                    ),
                },
            )
        )
    )


__all__ = ["message"]
