from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from bazaar_compute_node.app import localtime
from bazaar_compute_node.bcc import (
    build_parser,
    serialize_reminder_cancel,
    serialize_reminder_check,
    serialize_reminder_list,
    serialize_reminder_schedule,
    serialize_reminder_snooze,
    serialize_reminder_update,
)

REMINDER_ID = "019c1234-0000-7000-8000-000000000001"
ANCHOR_ID = "019c5678-0000-7000-8000-000000000001"
OCCURRENCE_ID = "019c9999-0000-7000-8000-000000000001"


@pytest.fixture(autouse=True)
def _use_utc_localtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(localtime, "get_localzone", lambda: ZoneInfo("UTC"))


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


def test_reminder_parser_exposes_only_the_six_supported_commands() -> None:
    parser = build_parser()
    for command in ("schedule", "check", "list", "snooze", "update", "cancel"):
        args = parser.parse_args(("reminder", command))
        assert args.resource == "reminder"
        assert args.command == command

    with pytest.raises(SystemExit):
        parser.parse_args(("reminder", "log"))
    with pytest.raises(SystemExit):
        parser.parse_args(("reminder", "schedule", "--channel", "dm:alice"))
    with pytest.raises(SystemExit):
        parser.parse_args(("reminder", "schedule", "--msg-id", ANCHOR_ID))


def test_reminder_schedule_serializer_matches_canonical_text() -> None:
    output = serialize_reminder_schedule({"reminder": reminder_payload()})
    assert output == (
        'Reminder scheduled: #019c1234 (one-time) "Inspect reminder"\n'
        "Next: 2027-01-15T08:00:00+00:00"
    )


def test_reminder_check_serializer_renders_due_snapshot_and_terminal_line() -> None:
    result = {
        "items": [
            {
                "occurrence": {
                    "occurrence_id": OCCURRENCE_ID,
                    "reminder_id": REMINDER_ID,
                    "owner_session_id": "bcn-a",
                    "occurrence_no": 3,
                    "anchor_message_id": ANCHOR_ID,
                    "scheduled_for_ms": 1_800_000_000_000,
                    "fired_at_ms": 1_800_000_001_000,
                    "next_fire_at_ms": 1_800_000_900_000,
                    "overdue": True,
                    "read_at_ms": 1_800_000_002_000,
                    "created_at_ms": 1_800_000_001_000,
                },
                "title": "Inspect reminder",
                "canonical_target": "dm:channel-a",
            }
        ],
        "has_more": False,
    }

    output = serialize_reminder_check(result)
    assert output == (
        "[class=due id=019c1234 occurrence=3 "
        "scheduled=2027-01-15T08:00:00+00:00 "
        "fired=2027-01-15T08:00:01+00:00 overdue=true "
        "next=2027-01-15T08:15:00+00:00 target=dm:channel-a "
        f"anchor={ANCHOR_ID}] Inspect reminder\n"
        "No more pending reminders."
    )


def test_reminder_check_serializer_distinguishes_empty_and_more() -> None:
    assert serialize_reminder_check({"items": [], "has_more": False}) == (
        "No pending reminders."
    )
    result = {
        "items": [
            {
                "occurrence": {
                    "occurrence_id": OCCURRENCE_ID,
                    "reminder_id": REMINDER_ID,
                    "owner_session_id": "bcn-a",
                    "occurrence_no": 1,
                    "anchor_message_id": ANCHOR_ID,
                    "scheduled_for_ms": 1_800_000_000_000,
                    "fired_at_ms": 1_800_000_000_000,
                    "next_fire_at_ms": None,
                    "overdue": False,
                    "read_at_ms": 1_800_000_000_001,
                    "created_at_ms": 1_800_000_000_000,
                },
                "title": "Inspect reminder",
                "canonical_target": "dm:channel-a",
            }
        ],
        "has_more": True,
    }
    assert serialize_reminder_check(result).endswith(
        "More pending reminders remain. Run `bcc reminder check` again."
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
    assert "#019c1234 [scheduled] (one-time) next=" in output
    assert "#019c1234 [fired] (one-time) fired_at=" in output
    assert "#019c1234 [canceled] (every:15m) canceled_at=" in output
    assert "+00:00" in output
    assert "anchor=019c5678" in output


def test_reminder_mutation_serializers_match_canonical_text() -> None:
    result = {"reminder": reminder_payload()}
    assert serialize_reminder_snooze(result).startswith(
        "Reminder snoozed: #019c1234\nNext: "
    )
    assert serialize_reminder_update(result).startswith(
        "Reminder updated: #019c1234\nNext: "
    )
    assert serialize_reminder_cancel(result) == "Reminder canceled: #019c1234"
