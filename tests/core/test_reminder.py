from __future__ import annotations

from datetime import datetime

import pytest

from bazaar_compute_node.core.models import Reminder, ReminderState
from bazaar_compute_node.core.reminder import (
    ReminderCancelRequest,
    ReminderListRequest,
    ReminderScheduleRequest,
    ReminderSnoozeRequest,
    ReminderUpdateRequest,
    canonical_id_reference,
    canonical_timezone,
    format_utc_timestamp,
    next_recurrence_ms,
    parse_duration,
    parse_fire_at,
    parse_repeat_rule,
    resolve_schedule,
)

_REMINDER_ID = "018f0000-0000-7000-8000-000000000001"
_MESSAGE_ID = "018f0000-0000-7000-8000-000000000002"


def utc_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def make_reminder(
    *,
    title: str = "Review the pull request",
    state: ReminderState = ReminderState.SCHEDULED,
    next_fire_at_ms: int | None = 2_000,
    repeat_rule: str | None = None,
    revision: int = 1,
    last_occurrence_no: int = 0,
    updated_at_ms: int = 1_000,
    last_fired_at_ms: int | None = None,
    canceled_at_ms: int | None = None,
) -> Reminder:
    return Reminder(
        reminder_id=_REMINDER_ID,
        owner_session_id="session-1",
        anchor_message_id=_MESSAGE_ID,
        title=title,
        state=state,
        next_fire_at_ms=next_fire_at_ms,
        repeat_rule=repeat_rule,
        timezone="UTC",
        revision=revision,
        last_occurrence_no=last_occurrence_no,
        created_at_ms=1_000,
        updated_at_ms=updated_at_ms,
        last_fired_at_ms=last_fired_at_ms,
        canceled_at_ms=canceled_at_ms,
    )


def test_duration_and_repeat_rules_parse_to_canonical_values() -> None:
    assert parse_duration("030m").canonical == "30m"
    assert parse_duration("2h").milliseconds == 7_200_000
    assert parse_duration("1d").milliseconds == 86_400_000

    every = parse_repeat_rule("every:015m")
    daily = parse_repeat_rule("daily@09:05")
    weekly = parse_repeat_rule("weekly:fri,mon,fri@09:05")

    assert every.canonical == "every:15m"
    assert daily.canonical == "daily@09:05"
    assert weekly.canonical == "weekly:mon,fri@09:05"
    assert weekly.weekdays == (0, 4)


@pytest.mark.parametrize(
    "value",
    ["", "0m", "1s", "1.5h", " 1h", "1h ", "every:1h"],
)
def test_duration_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(value)


@pytest.mark.parametrize(
    "value",
    [
        "every:0m",
        "every:1s",
        "daily@24:00",
        "daily@09:60",
        "weekly:@09:00",
        "weekly:monday@09:00",
        "weekly:mon,@09:00",
        "0 9 * * *",
        "EVERY:1h",
    ],
)
def test_repeat_rule_rejects_values_outside_the_supported_grammar(value: str) -> None:
    with pytest.raises(ValueError):
        parse_repeat_rule(value)


def test_schedule_resolves_one_time_and_repeat_first_slots() -> None:
    now_ms = utc_ms("2026-08-15T10:00:00Z")

    relative = resolve_schedule(evaluated_at_ms=now_ms, delay_seconds=30)
    absolute = resolve_schedule(
        evaluated_at_ms=now_ms,
        fire_at="2026-08-15T13:00:00+02:00",
    )
    interval = resolve_schedule(
        evaluated_at_ms=now_ms,
        repeat_rule="every:15m",
    )
    daily = resolve_schedule(
        evaluated_at_ms=now_ms,
        repeat_rule="daily@09:00",
        timezone="Asia/Shanghai",
    )

    assert relative.next_fire_at_ms == now_ms + 30_000
    assert relative.timezone == "UTC"
    assert absolute.next_fire_at_ms == now_ms + 3_600_000
    assert interval.next_fire_at_ms == now_ms + 900_000
    assert interval.repeat_rule == "every:15m"
    assert daily.next_fire_at_ms == utc_ms("2026-08-16T01:00:00Z")


# An absolute fire time equal to now parses, but schedule rejects it as not future.
def test_fire_at_requires_an_offset_and_schedule_requires_future_time() -> None:
    assert parse_fire_at("2026-08-15T12:00:00+02:00") == utc_ms("2026-08-15T10:00:00Z")
    assert format_utc_timestamp(utc_ms("2026-08-15T10:00:00.123Z")) == (
        "2026-08-15T10:00:00.123Z"
    )

    with pytest.raises(ValueError, match="offset"):
        parse_fire_at("2026-08-15T10:00:00")
    with pytest.raises(ValueError, match="future"):
        resolve_schedule(
            evaluated_at_ms=utc_ms("2026-08-15T10:00:00Z"),
            fire_at="2026-08-15T10:00:00Z",
        )


def test_schedule_rejects_time_conflicts_and_invalid_timezones() -> None:
    now_ms = utc_ms("2026-08-15T10:00:00Z")
    with pytest.raises(ValueError, match="mutually exclusive"):
        resolve_schedule(
            evaluated_at_ms=now_ms,
            delay_seconds=30,
            fire_at="2026-08-15T11:00:00Z",
        )
    with pytest.raises(ValueError, match="requires"):
        resolve_schedule(evaluated_at_ms=now_ms)
    with pytest.raises(ValueError, match="timezone"):
        canonical_timezone("Mars/Olympus_Mons")


def test_calendar_recurrence_uses_first_ambiguous_dst_instant() -> None:
    before_slot = utc_ms("2026-11-01T04:00:00Z")
    slot = resolve_schedule(
        evaluated_at_ms=before_slot,
        repeat_rule="daily@01:30",
        timezone="America/New_York",
    ).next_fire_at_ms

    assert slot == utc_ms("2026-11-01T05:30:00Z")
    assert next_recurrence_ms(
        scheduled_for_ms=slot,
        repeat_rule="daily@01:30",
        timezone="America/New_York",
    ) == utc_ms("2026-11-02T06:30:00Z")


def test_calendar_recurrence_moves_nonexistent_dst_time_to_gap_end() -> None:
    before_slot = utc_ms("2026-03-08T05:00:00Z")
    slot = resolve_schedule(
        evaluated_at_ms=before_slot,
        repeat_rule="daily@02:30",
        timezone="America/New_York",
    ).next_fire_at_ms

    assert slot == utc_ms("2026-03-08T07:00:00Z")
    assert next_recurrence_ms(
        scheduled_for_ms=slot,
        repeat_rule="daily@02:30",
        timezone="America/New_York",
    ) == utc_ms("2026-03-09T06:30:00Z")


def test_weekly_next_uses_canonical_weekday_order() -> None:
    slot = resolve_schedule(
        evaluated_at_ms=utc_ms("2026-08-15T10:00:00Z"),  # Saturday
        repeat_rule="weekly:fri,mon@09:00",
        timezone="UTC",
    )
    assert slot.repeat_rule == "weekly:mon,fri@09:00"
    assert slot.next_fire_at_ms == utc_ms("2026-08-17T09:00:00Z")


def test_one_time_and_recurring_fire_follow_the_state_contract() -> None:
    one_time = make_reminder()
    fired = one_time.record_fire(
        scheduled_for_ms=2_000,
        fired_at_ms=2_010,
        next_fire_at_ms=None,
    )
    assert fired.state is ReminderState.FIRED
    assert fired.next_fire_at_ms is None
    assert fired.revision == 2
    assert fired.last_occurrence_no == 1

    recurring = make_reminder(repeat_rule="every:15m")
    advanced = recurring.record_fire(
        scheduled_for_ms=2_000,
        fired_at_ms=10_000,
        next_fire_at_ms=902_000,
    )
    assert advanced.state is ReminderState.SCHEDULED
    assert advanced.next_fire_at_ms == 902_000
    assert advanced.last_occurrence_no == 1


def test_fired_reminder_can_be_snoozed_but_not_updated_or_canceled() -> None:
    fired = make_reminder(
        state=ReminderState.FIRED,
        next_fire_at_ms=None,
        revision=2,
        last_occurrence_no=1,
        updated_at_ms=2_010,
        last_fired_at_ms=2_010,
    )

    snoozed = fired.snooze(duration_ms=30_000, at_ms=3_000)
    assert snoozed.state is ReminderState.SCHEDULED
    assert snoozed.next_fire_at_ms == 33_000
    assert snoozed.revision == 3

    with pytest.raises(ValueError, match="scheduled"):
        fired.update_title("Changed", at_ms=3_000)
    with pytest.raises(ValueError, match="scheduled"):
        fired.cancel(at_ms=3_000)


def test_canceled_reminder_rejects_all_mutations() -> None:
    canceled = make_reminder().cancel(at_ms=1_500)
    assert canceled.state is ReminderState.CANCELED

    with pytest.raises(ValueError, match="canceled"):
        canceled.snooze(duration_ms=1_000, at_ms=2_000)
    with pytest.raises(ValueError, match="scheduled"):
        canceled.update_next_fire(3_000, at_ms=2_000)
    with pytest.raises(ValueError, match="scheduled"):
        canceled.cancel(at_ms=2_000)
    with pytest.raises(ValueError, match="scheduled"):
        canceled.record_fire(
            scheduled_for_ms=2_000,
            fired_at_ms=2_000,
            next_fire_at_ms=None,
        )


def test_title_and_model_field_combinations_fail_closed() -> None:
    with pytest.raises(ValueError, match="control"):
        ReminderScheduleRequest.from_options(
            title="line one\nline two",
            message_id=_MESSAGE_ID,
            evaluated_at_ms=1_000,
            delay_seconds=1,
        )
    with pytest.raises(ValueError, match="next_fire_at_ms"):
        make_reminder(next_fire_at_ms=None)
    with pytest.raises(ValueError, match="recurring"):
        make_reminder(
            state=ReminderState.FIRED,
            next_fire_at_ms=None,
            repeat_rule="every:1h",
            revision=2,
            last_occurrence_no=1,
            updated_at_ms=2_000,
            last_fired_at_ms=2_000,
        )


def test_request_contracts_validate_ids_limits_and_exactly_one_update() -> None:
    with pytest.raises(ValueError, match="UUID"):
        canonical_id_reference("not-an-id")

    assert ReminderListRequest().statuses == frozenset(
        {ReminderState.SCHEDULED, ReminderState.FIRED}
    )
    snooze = ReminderSnoozeRequest.from_options(
        reminder_id=_REMINDER_ID,
        duration="30m",
        evaluated_at_ms=1_000,
    )
    assert snooze.duration_ms == 1_800_000
    assert ReminderCancelRequest(_REMINDER_ID, 1_000).reminder_id == _REMINDER_ID

    with pytest.raises(ValueError, match="exactly one"):
        ReminderUpdateRequest.from_options(
            reminder_id=_REMINDER_ID,
            evaluated_at_ms=1_000,
            title="Changed",
            cadence="every:1h",
        )
    update = ReminderUpdateRequest.from_options(
        reminder_id=_REMINDER_ID,
        evaluated_at_ms=1_000,
        cadence="every:01h",
    )
    assert update.repeat_rule == "every:1h"


def test_transition_times_and_update_next_fire_must_move_forward() -> None:
    reminder = make_reminder()
    with pytest.raises(ValueError, match="backwards"):
        reminder.update_title("Changed", at_ms=999)
    with pytest.raises(ValueError, match="future"):
        reminder.update_next_fire(1_500, at_ms=1_500)
    with pytest.raises(ValueError, match="backwards"):
        reminder.record_fire(
            scheduled_for_ms=2_000,
            fired_at_ms=999,
            next_fire_at_ms=None,
        )


def test_result_contracts_reject_values_from_other_domains() -> None:
    from bazaar_compute_node.core.reminder import (
        ReminderCancelResult,
        ReminderScheduleResult,
        ReminderSnoozeResult,
        ReminderUpdateResult,
    )

    for result_type in (
        ReminderScheduleResult,
        ReminderSnoozeResult,
        ReminderUpdateResult,
        ReminderCancelResult,
    ):
        with pytest.raises(TypeError, match="Reminder"):
            result_type("not-a-reminder")  # type: ignore[arg-type]
