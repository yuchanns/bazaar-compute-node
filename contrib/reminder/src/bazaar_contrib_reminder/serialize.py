from __future__ import annotations

from .models import Reminder


def serialize_reminder(reminder: Reminder) -> dict[str, object]:
    return {
        "reminder_id": reminder.reminder_id,
        "owner_thread_id": reminder.owner_thread_id,
        "anchor_message_id": reminder.anchor_message_id,
        "title": reminder.title,
        "state": reminder.state.value,
        "next_fire_at_ms": reminder.next_fire_at_ms,
        "repeat_rule": reminder.repeat_rule,
        "timezone": reminder.timezone,
        "revision": reminder.revision,
        "last_occurrence_no": reminder.last_occurrence_no,
        "created_at_ms": reminder.created_at_ms,
        "updated_at_ms": reminder.updated_at_ms,
        "last_fired_at_ms": reminder.last_fired_at_ms,
        "canceled_at_ms": reminder.canceled_at_ms,
    }
