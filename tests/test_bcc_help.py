from __future__ import annotations

import pytest

from bazaar_compute_node.bcc import build_parser


@pytest.mark.parametrize(
    ("argv", "expected"),
    (
        (("--help",), ("resources:", "message", "inbox", "thread", "reminder")),
        (
            ("message", "--help"),
            ("Message operations", "message commands:", "check", "read", "send"),
        ),
        (
            ("message", "check", "--help"),
            ("agent inbox (non-blocking)", "Acks delivered seqs"),
        ),
        (
            ("message", "read", "--help"),
            ("--target <target>", "--around <message-id>", "--limit <n>"),
        ),
        (
            ("message", "send", "--help"),
            ("body is read from stdin", "--attachment <path>"),
        ),
        (
            ("inbox", "--help"),
            ("Inbox discovery operations", "inbox commands:", "list"),
        ),
        (
            ("inbox", "list", "--help"),
            (
                "List available message targets",
                "--limit <n>",
                "--offset <n>",
            ),
        ),
        (
            ("thread", "--help"),
            ("Thread attention operations", "thread commands:", "unfollow"),
        ),
        (
            ("thread", "unfollow", "--help"),
            ("--target <target>", "does not affect Reminder ownership"),
        ),
        (
            ("reminder", "--help"),
            (
                "Reminder operations",
                "reminder commands:",
                "schedule",
                "check",
                "list",
                "snooze",
                "update",
                "cancel",
            ),
        ),
        (
            ("reminder", "schedule", "--help"),
            (
                "--title <t>",
                "--delay-seconds <n>",
                "--fire-at <iso>",
                "--repeat <rule>",
                "--tz <iana>",
                "--message-id <id>",
                "weekly:mon,fri@09:00",
            ),
        ),
        (
            ("reminder", "check", "--help"),
            (
                "up to 100 pending Reminder occurrences",
                "mark exactly the returned occurrences as read",
            ),
        ),
        (
            ("reminder", "list", "--help"),
            (
                "--all",
                "--status <scheduled,fired,canceled>",
                "defaults to scheduled and fired",
            ),
        ),
        (
            ("reminder", "snooze", "--help"),
            ("--id <id>", "--by <duration>", "Snooze duration, e.g. 30m, 2h, 1d"),
        ),
        (
            ("reminder", "update", "--help"),
            (
                "--fire-at <iso>",
                "--in <duration>",
                "--cadence <rule>",
                "New reminder title",
            ),
        ),
        (
            ("reminder", "cancel", "--help"),
            (
                "--id <id>",
                "Cancel a scheduled reminder by id (full uuid)",
            ),
        ),
    ),
)
def test_bcc_help_is_available_at_every_command_level(
    argv: tuple[str, ...],
    expected: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exit_error:
        parser.parse_args(argv)

    assert exit_error.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    for snippet in expected:
        assert snippet in output


def test_reminder_help_does_not_advertise_removed_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(("reminder", "--help"))
    resource_help = capsys.readouterr().out

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(("reminder", "schedule", "--help"))
    schedule_help = capsys.readouterr().out

    assert " log" not in resource_help
    assert "--channel" not in schedule_help
    assert "--msg-id" not in schedule_help
