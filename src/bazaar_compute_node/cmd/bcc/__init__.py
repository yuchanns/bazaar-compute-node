from __future__ import annotations

import os
from collections.abc import Sequence

import click

from ...core.actor import Mode
from ._client import BccCommandError
from ._format import print_error
from .inbox import inbox
from .message import message
from .node import node
from .reminder import reminder
from .thread import thread


@click.group(
    help=(
        "Session-scoped collaboration commands for a Bazaar Compute Node. "
        "Use these commands from the current agent session to inspect messages, "
        "send replies, manage thread attention, and schedule persistent reminders."
    ),
    epilog=(
        "Run `bcc <resource> --help` or "
        "`bcc <resource> <command> --help` for command-specific usage."
    ),
    context_settings={"help_option_names": ["-h", "--help"]},
)
def bcc() -> None: ...


for group in (message, thread, reminder):
    bcc.add_command(group)

if os.environ.get("BCN_AGENT_MODE") == Mode.DANGEROUS_INDIVIDUAL.value:
    bcc.add_command(inbox)

# Windows has nothing that brings the node back after an upgrade exits it, so
# there the node offers no upgrade and the commands would only ever be refused
if os.name != "nt":
    bcc.add_command(node)
    # `bcc upgrade` reads better than `bcc node upgrade`, so the command line name
    # and the resource it addresses differ here
    bcc.add_command(node.commands["upgrade"], "upgrade")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        bcc.main(
            args=list(argv) if argv is not None else None,
            prog_name="bcc",
            standalone_mode=False,
        )
    except BccCommandError as error:
        print_error(error)
        return 1
    except click.ClickException as error:
        error.show()
        return error.exit_code
    except click.Abort:
        return 1
    return 0


__all__ = ["bcc", "main"]
