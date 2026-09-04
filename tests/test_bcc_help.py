from __future__ import annotations

import click
import pytest

from bazaar_compute_node.cmd.bcc import bcc


def render_help(path: tuple[str, ...]) -> str:
    command: click.Command = bcc
    context = click.Context(bcc, info_name="bcc")
    for name in path:
        assert isinstance(command, click.Group)
        command = command.commands[name]
        context = click.Context(command, parent=context, info_name=name)
    return " ".join(command.get_help(context).split())


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ((), ("Commands:", "message", "thread", "reminder")),
        (("message",), ("Message operations", "check", "read", "send")),
        (
            ("message", "check"),
            ("agent inbox (non-blocking)", "Acks delivered seqs"),
        ),
        (
            ("message", "read"),
            ("--target <target>", "--around <message-id>", "--limit <n>"),
        ),
        (
            ("message", "send"),
            ("body is read from stdin", "--attachment <path>"),
        ),
        (("thread",), ("Thread attention operations", "unfollow")),
        (("thread", "unfollow"), ("--target <target>",)),
        (("reminder",), ("Reminder operations", "schedule", "snooze", "cancel")),
        (
            ("reminder", "schedule"),
            ("--title <t>", "--delay-seconds <n>", "--message-id <uuid>"),
        ),
        (("reminder", "list"), ("--all", "--status <scheduled,fired,canceled>")),
        (("reminder", "snooze"), ("--id <id>", "--by <duration>")),
        (("reminder", "update"), ("--cadence <rule>", "--in <duration>")),
        (
            ("reminder", "cancel"),
            ("--id <id>", "Cancel a scheduled reminder by id (full uuid)"),
        ),
    ),
)
def test_bcc_help_is_available_at_every_command_level(
    path: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    output = render_help(path)

    for snippet in expected:
        assert snippet in output
