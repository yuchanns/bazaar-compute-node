from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import cast

import click

from ..reminder import format_utc_timestamp
from ..rendering import TextTemplate
from ._client import BccCommandError

_REMINDER_SCHEDULE = TextTemplate.from_resource("bcc/reminder_schedule.tpl")
_REMINDER_LIST = TextTemplate.from_resource("bcc/reminder_list.tpl")
_REMINDER_MUTATION = TextTemplate.from_resource("bcc/reminder_mutation.tpl")
_ERROR = TextTemplate.from_resource("bcc/error.tpl")


def echo(text: str) -> None:
    click.echo(text)


def print_error(error: BccCommandError) -> None:
    click.echo(
        _ERROR.render(
            {
                "message": str(error),
                "code": error.code,
                "draft_saved": error.draft_saved,
                "next_action": error.next_action,
            }
        ),
        file=sys.stderr,
    )


def _reminder(result: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], result["reminder"])


def _quoted_title(reminder: Mapping[str, object]) -> str:
    return json.dumps(cast(str, reminder["title"]), ensure_ascii=False)


def serialize_reminder_schedule(result: Mapping[str, object]) -> str:
    reminder = _reminder(result)
    return _REMINDER_SCHEDULE.render(
        {
            "reminder_id": cast(str, reminder["reminder_id"]),
            "repeat_rule": reminder["repeat_rule"],
            "title": _quoted_title(reminder),
            "next_fire": format_utc_timestamp(cast(int, reminder["next_fire_at_ms"])),
        }
    )


def serialize_reminder_list(result: Mapping[str, object]) -> str:
    reminders = cast(list[Mapping[str, object]], result["reminders"])
    rendered: list[dict[str, object]] = []
    for reminder in reminders:
        state = cast(str, reminder["state"])
        timestamp_key = {
            "scheduled": "next_fire_at_ms",
            "fired": "last_fired_at_ms",
            "canceled": "canceled_at_ms",
        }.get(state)
        if timestamp_key is None:
            continue
        rendered.append(
            {
                "reminder_id": cast(str, reminder["reminder_id"]),
                "state": state,
                "repeat_rule": reminder["repeat_rule"],
                "timestamp": format_utc_timestamp(cast(int, reminder[timestamp_key])),
                "title": _quoted_title(reminder),
                "anchor": cast(str, reminder["anchor_message_id"]),
            }
        )
    return _REMINDER_LIST.render({"reminders": rendered})


def _serialize_reminder_mutation(
    result: Mapping[str, object],
    *,
    verb: str,
    include_next: bool,
) -> str:
    reminder = _reminder(result)
    return _REMINDER_MUTATION.render(
        {
            "verb": verb,
            "reminder_id": cast(str, reminder["reminder_id"]),
            "next_fire": (
                format_utc_timestamp(cast(int, reminder["next_fire_at_ms"]))
                if include_next
                else None
            ),
        }
    )


def serialize_reminder_snooze(result: Mapping[str, object]) -> str:
    return _serialize_reminder_mutation(result, verb="snoozed", include_next=True)


def serialize_reminder_update(result: Mapping[str, object]) -> str:
    return _serialize_reminder_mutation(result, verb="updated", include_next=True)


def serialize_reminder_cancel(result: Mapping[str, object]) -> str:
    return _serialize_reminder_mutation(result, verb="canceled", include_next=False)
