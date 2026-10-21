from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from html import escape
from typing import Annotated, cast

import click
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from ..rendering import TextTemplate
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


_INBOX_CHECK = TextTemplate.from_resource("bcc/messages/inbox_check.tpl")


_CHECK = TextTemplate.from_resource("bcc/messages/check.tpl")


_READ = TextTemplate.from_resource("bcc/messages/read.tpl")


_SEARCH = TextTemplate.from_resource("bcc/messages/search.tpl")


_UNFOLLOW = TextTemplate.from_resource("bcc/messages/unfollow.tpl")


_ERROR = TextTemplate.from_resource("bcc/messages/error.tpl")


def format_message_time(timestamp_ms: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )


_ATTACHMENT_SUFFIX = TextTemplate.from_resource(
    "bcc/messages/command/attachment_suffix.tpl"
)
_SENDER = TextTemplate.from_resource("bcc/messages/command/sender.tpl")
_CHECK_MESSAGE = TextTemplate.from_resource("bcc/messages/command/check_message.tpl")
_READ_MESSAGE = TextTemplate.from_resource("bcc/messages/command/read_message.tpl")


def _message_timestamp(message: Mapping[str, object]) -> int:
    for field_name in ("provider_time_ms", "received_at_ms", "created_at_ms"):
        timestamp = message.get(field_name)
        if timestamp is not None:
            return cast(int, timestamp)
    raise ValueError("message has no display timestamp")


def _message_header_fields(
    message: Mapping[str, object],
) -> tuple[str, str, str, str, str | None, str]:
    target = cast(str, message["target"])
    message_id = cast(str, message["message_id"])
    sender_kind = cast(str, message["sender_kind"])
    sender_value = message["sender"]
    sender: str | None = None
    if sender_value is not None:
        sender_mapping = cast(Mapping[str, object], sender_value)
        sender = (
            _SENDER.render(
                {
                    "sender_id": cast(str | None, sender_mapping.get("id")),
                    "sender_name": cast(str | None, sender_mapping.get("name")),
                    "sender_display_name": cast(
                        str | None, sender_mapping.get("display_name")
                    ),
                }
            )
            or None
        )
    return (
        target,
        message_id,
        format_message_time(_message_timestamp(message)),
        sender_kind,
        sender,
        cast(str, message["body"]),
    )


def _attachment_suffix(message: Mapping[str, object]) -> str:
    attachments = cast(list[Mapping[str, object]], message["attachments"])
    return _ATTACHMENT_SUFFIX.render(
        {
            "attachments": [
                {
                    "name": cast(str, attachment["name"]),
                    "attachment_id": cast(str | None, attachment.get("attachment_id")),
                    "state": cast(str, attachment["state"]),
                    "path": attachment.get("relative_path"),
                    "error": attachment.get("error"),
                }
                for attachment in attachments
            ]
        }
    )


def format_check_message(message: Mapping[str, object]) -> str:
    target, message_id, timestamp, sender_kind, sender, body = _message_header_fields(
        message
    )
    source_target = message.get("system_message_source_target")
    source_message_id = message.get("system_message_source_message_id")
    return _CHECK_MESSAGE.render(
        {
            "target": target,
            "message_id": message_id,
            "timestamp": timestamp,
            "sender_kind": sender_kind,
            "reply_to_message_id": message["reply_to_message_id"],
            "sender": sender,
            "body": body,
            "attachment_suffix": _attachment_suffix(message),
            "system_message_kind": message.get("system_message_kind"),
            # a repeating reminder is the only one that can still be updated
            "repeats": "\nNext iteration: " in body,
            "source_target": (
                json.dumps(cast(str, source_target))
                if source_target is not None
                else None
            ),
            "source_message_id": (
                json.dumps(cast(str, source_message_id))
                if source_message_id is not None
                else None
            ),
        }
    )


def format_read_message(
    message: Mapping[str, object],
    *,
    index: int,
    count: int,
) -> str:
    target, message_id, timestamp, sender_kind, sender, body = _message_header_fields(
        message
    )
    return _READ_MESSAGE.render(
        {
            "index": index,
            "count": count,
            "seq": cast(int, message["seq"]),
            "message_id": message_id,
            "timestamp": timestamp,
            "sender_kind": sender_kind,
            "target": target,
            "reply_to_message_id": message["reply_to_message_id"],
            "sender": sender,
            "body": body,
            "attachment_suffix": _attachment_suffix(message),
        }
    )


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


def _search_text(text: str, *, references: bool = True) -> str:
    text = re.sub(
        r"</?(?:result|preview|match)\b[^>]*>|<omit\s*/>",
        lambda match: escape(match[0], quote=False),
        text,
        flags=re.IGNORECASE,
    )
    if not references:
        return text
    text = re.sub(r"\bdm:@([A-Za-z0-9][A-Za-z0-9_-]*)", r"dm:user:\1", text)
    text = re.sub(r"\btask #([0-9]+)\b", r"task:\1", text)
    text = re.sub(r"(^|[\n\s([{\"'`;])@([A-Za-z0-9][A-Za-z0-9_-]*)", r"\1user:\2", text)
    return re.sub(r"(^|[\n\s([{\"'`;])#([A-Za-z][A-Za-z0-9_-]*)", r"\1channel:\2", text)


def serialize_search(result: Mapping[str, object]) -> str:
    messages = cast(list[Mapping[str, object]], result["messages"])
    hits: list[dict[str, object]] = []
    for message in messages:
        sender = cast(Mapping[str, object] | None, message["sender"])
        timestamp = datetime.fromtimestamp(
            cast(int, message["at_ms"]) / 1000, tz=UTC
        ).astimezone()
        offset = timestamp.strftime("%z")
        hits.append(
            {
                "source": _search_text(cast(str, message["target"]), references=False),
                "message_id": message["message_id"],
                "timestamp": (
                    f"{timestamp:%Y-%m-%d %H:%M:%S} {offset[:3]}:{offset[3:]}"
                ),
                "sender_kind": message["sender_kind"],
                "sender": _search_text(
                    "self"
                    if message["direction"] == "outbound"
                    else cast(str, sender.get("name") or sender.get("id") or "unknown")
                    if sender is not None
                    else "unknown"
                ),
                "preview": "".join(
                    "<match>" + _search_text(text) + "</match>"
                    if matched
                    else _search_text(text)
                    for text, matched in cast(list[tuple[str, bool]], message["parts"])
                ),
            }
        )
    return _SEARCH.render({**result, "messages": hits})


def serialize_unfollow(result: Mapping[str, object]) -> str:
    return _UNFOLLOW.render(
        {
            "target": cast(str, result["target"]),
            "changed": cast(bool, result["changed"]),
        }
    )
