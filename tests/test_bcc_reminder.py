from __future__ import annotations

import click

from bazaar_compute_node.cmd.bcc import bcc
from bazaar_compute_node.cmd.bcc._format import (
    serialize_reminder_cancel,
    serialize_reminder_list,
    serialize_reminder_schedule,
    serialize_reminder_snooze,
    serialize_reminder_update,
)

REMINDER_ID = "019c1234-0000-7000-8000-000000000001"
ANCHOR_ID = "019c5678-0000-7000-8000-000000000001"


def subcommand(group: click.Command, *path: str) -> click.Command:
    resolved = group
    for name in path:
        assert isinstance(resolved, click.Group)
        resolved = resolved.commands[name]
    return resolved


def reminder_payload(
    *,
    state: str = "scheduled",
    next_fire_at_ms: int | None = 1_800_000_000_000,
    repeat_rule: str | None = None,
    last_fired_at_ms: int | None = None,
    canceled_at_ms: int | None = None,
) -> dict[str, object]:
    return {
        "reminder_id": REMINDER_ID,
        "owner_session_id": "bcn-a",
        "anchor_message_id": ANCHOR_ID,
        "title": "Inspect reminder",
        "state": state,
        "next_fire_at_ms": next_fire_at_ms,
        "repeat_rule": repeat_rule,
        "timezone": "UTC",
        "revision": 1,
        "last_occurrence_no": 0,
        "created_at_ms": 1_799_999_000_000,
        "updated_at_ms": 1_799_999_000_000,
        "last_fired_at_ms": last_fired_at_ms,
        "canceled_at_ms": canceled_at_ms,
    }


def test_reminder_exposes_only_the_five_supported_commands() -> None:
    reminder = subcommand(bcc, "reminder")
    assert isinstance(reminder, click.Group)

    assert set(reminder.commands) == {
        "schedule",
        "list",
        "snooze",
        "update",
        "cancel",
    }
    schedule = {
        parameter.name for parameter in subcommand(bcc, "reminder", "schedule").params
    }
    assert "channel" not in schedule
    assert "msg_id" not in schedule


def test_reminder_schedule_serializer_matches_text() -> None:
    output = serialize_reminder_schedule({"reminder": reminder_payload()})
    assert output == (
        f'Reminder scheduled: #{REMINDER_ID} (one-time) "Inspect reminder"\n'
        "Next: 2027-01-15T08:00:00.000Z"
    )


def test_reminder_list_serializer_renders_definition_states() -> None:
    result = {
        "reminders": [
            reminder_payload(),
            reminder_payload(
                state="fired",
                next_fire_at_ms=None,
                last_fired_at_ms=1_800_000_001_000,
            ),
            reminder_payload(
                state="canceled",
                next_fire_at_ms=None,
                repeat_rule="every:15m",
                canceled_at_ms=1_800_000_002_000,
            ),
        ]
    }
    output = serialize_reminder_list(result)
    assert f"#{REMINDER_ID} [scheduled] (one-time) next=" in output
    assert f"#{REMINDER_ID} [fired] (one-time) fired_at=" in output
    assert f"#{REMINDER_ID} [canceled] (every:15m) canceled_at=" in output
    assert f"anchor={ANCHOR_ID}" in output


def test_reminder_mutation_serializers_match_text() -> None:
    result = {"reminder": reminder_payload()}
    assert serialize_reminder_snooze(result).startswith(
        f"Reminder snoozed: #{REMINDER_ID}\nNext: "
    )
    assert serialize_reminder_update(result).startswith(
        f"Reminder updated: #{REMINDER_ID}\nNext: "
    )
    assert serialize_reminder_cancel(result) == f"Reminder canceled: #{REMINDER_ID}"


def test_reminder_list_says_so_when_there_is_nothing_scheduled() -> None:
    # case: an agent with no follow-ups gets a sentence, not an empty line
    assert serialize_reminder_list({"reminders": []}) == "No reminders."
