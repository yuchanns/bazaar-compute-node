from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Annotated, cast

import click
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from ... import __distribution__
from ...app.command import (
    format_check_message,
    format_read_message,
)
from ...core.reminder import format_utc_timestamp
from ...rendering import TextTemplate
from ._client import BccCommandError


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


_INBOX_CHECK = TextTemplate.from_resource("bcc/inbox_check.tpl")
_CHECK = TextTemplate.from_resource("bcc/check.tpl")
_READ = TextTemplate.from_resource("bcc/read.tpl")
_UNFOLLOW = TextTemplate.from_resource("bcc/unfollow.tpl")
_REMINDER_SCHEDULE = TextTemplate.from_resource("bcc/reminder_schedule.tpl")
_REMINDER_LIST = TextTemplate.from_resource("bcc/reminder_list.tpl")
_REMINDER_MUTATION = TextTemplate.from_resource("bcc/reminder_mutation.tpl")
_UPGRADE = TextTemplate.from_resource("bcc/upgrade.tpl")
_VERSION = TextTemplate.from_resource("bcc/version.tpl")
_ERROR = TextTemplate.from_resource("bcc/error.tpl")


# `bcc upgrade` reads better than `bcc node upgrade`, so the command line name
# and the resource it addresses differ here


def serialize_inbox_check(result: Mapping[str, object]) -> str:
    targets = cast(list[Mapping[str, object]], result["targets"])
    return _INBOX_CHECK.render(
        {
            "target_lines": [
                " ".join(
                    (
                        f"[target={target['target']}",
                        f"pending={target['pending_count']}",
                        f"latest-msg={target['latest_message_id']}]",
                    )
                )
                for target in targets
            ]
        }
    )


def serialize_check(result: Mapping[str, object]) -> str:
    messages = cast(list[Mapping[str, object]], result["messages"])
    referenced_messages = cast(
        list[Mapping[str, object]], result["referenced_messages"]
    )
    return _CHECK.render(
        {
            "referenced_lines": [
                format_read_message(
                    message,
                    index=index,
                    count=len(referenced_messages),
                )
                for index, message in enumerate(referenced_messages, start=1)
            ],
            "message_lines": [format_check_message(message) for message in messages],
        }
    )


def serialize_read(result: Mapping[str, object]) -> str:
    messages = cast(list[Mapping[str, object]], result["messages"])
    referenced_messages = cast(
        list[Mapping[str, object]], result["referenced_messages"]
    )
    return _READ.render(
        {
            "shown": len(messages),
            "first_seq": result["first_seq"] if messages else None,
            "last_seq": result["last_seq"] if messages else None,
            "referenced_lines": [
                format_read_message(
                    message,
                    index=index,
                    count=len(referenced_messages),
                )
                for index, message in enumerate(referenced_messages, start=1)
            ],
            "message_lines": [
                format_read_message(message, index=index, count=len(messages))
                for index, message in enumerate(messages, start=1)
            ],
        }
    )


class _MessageSendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: Annotated[StrictStr, Field(min_length=1)]


def serialize_send(result: Mapping[str, object]) -> str:
    try:
        return _MessageSendResponse.model_validate(result).text
    except ValidationError as error:
        raise BccCommandError(
            "Message send returned an invalid response.",
            code="SEND_RESPONSE_INVALID",
        ) from error


def serialize_version(result: Mapping[str, object]) -> str:
    return _VERSION.render(
        {
            "distribution": __distribution__,
            "version": cast(str, result["version"]),
        }
    )


def serialize_upgrade(result: Mapping[str, object]) -> str:
    return _UPGRADE.render(
        {
            "distribution": __distribution__,
            "installed_version": cast(str, result["installed_version"]),
            "upgrade_version": cast(str, result["upgrade_version"]),
            "reminder_id": cast(str | None, result["reminder_id"]),
        }
    )


def serialize_unfollow(result: Mapping[str, object]) -> str:
    return _UNFOLLOW.render(
        {
            "target": cast(str, result["target"]),
            "changed": cast(bool, result["changed"]),
        }
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
