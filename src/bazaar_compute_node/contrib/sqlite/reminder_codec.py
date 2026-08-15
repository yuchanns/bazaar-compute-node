from __future__ import annotations

import aiosqlite

from ...core.models import Reminder, ReminderOccurrence, ReminderState
from .codec import (
    _optional_non_negative_int,
    _optional_text,
    _required_boolean,
    _required_non_negative_int,
    _required_positive_int,
    _required_text,
)


def reminder_from_row(row: aiosqlite.Row) -> Reminder:
    return Reminder(
        reminder_id=_required_text(row["reminder_id"], "reminder_id"),
        owner_session_id=_required_text(row["owner_session_id"], "owner_session_id"),
        anchor_message_id=_required_text(row["anchor_message_id"], "anchor_message_id"),
        title=_required_text(row["title"], "title"),
        state=ReminderState(_required_text(row["state"], "reminder.state")),
        next_fire_at_ms=_optional_non_negative_int(
            row["next_fire_at_ms"], "next_fire_at_ms"
        ),
        repeat_rule=_optional_text(row["repeat_rule"], "repeat_rule"),
        timezone=_required_text(row["timezone"], "timezone"),
        revision=_required_positive_int(row["revision"], "revision"),
        last_occurrence_no=_required_non_negative_int(
            row["last_occurrence_no"], "last_occurrence_no"
        ),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        updated_at_ms=_required_non_negative_int(row["updated_at_ms"], "updated_at_ms"),
        last_fired_at_ms=_optional_non_negative_int(
            row["last_fired_at_ms"], "last_fired_at_ms"
        ),
        canceled_at_ms=_optional_non_negative_int(
            row["canceled_at_ms"], "canceled_at_ms"
        ),
    )


def reminder_occurrence_from_row(row: aiosqlite.Row) -> ReminderOccurrence:
    return ReminderOccurrence(
        occurrence_id=_required_text(row["occurrence_id"], "occurrence_id"),
        reminder_id=_required_text(row["reminder_id"], "reminder_id"),
        owner_session_id=_required_text(row["owner_session_id"], "owner_session_id"),
        occurrence_no=_required_positive_int(row["occurrence_no"], "occurrence_no"),
        anchor_message_id=_required_text(row["anchor_message_id"], "anchor_message_id"),
        scheduled_for_ms=_required_non_negative_int(
            row["scheduled_for_ms"], "scheduled_for_ms"
        ),
        fired_at_ms=_required_non_negative_int(row["fired_at_ms"], "fired_at_ms"),
        next_fire_at_ms=_optional_non_negative_int(
            row["next_fire_at_ms"], "next_fire_at_ms"
        ),
        overdue=bool(_required_boolean(row["overdue"], "overdue")),
        read_at_ms=_optional_non_negative_int(row["read_at_ms"], "read_at_ms"),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
    )


__all__ = ["reminder_from_row", "reminder_occurrence_from_row"]
