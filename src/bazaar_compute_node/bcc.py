from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, NoReturn, cast
from uuid import uuid7

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from .app.command import format_check_message, format_message_time, format_read_message
from .app.transport import LocalCommandClient
from .core.reminder import canonical_id_reference, format_utc_timestamp


class BccCommandError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        draft_saved: bool = False,
        next_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.draft_saved = draft_saved
        self.next_action = next_action


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bcc",
        description=(
            "Session-scoped collaboration commands for a Bazaar Compute Node. "
            "Use these commands from the current agent session to inspect messages, "
            "send replies, hand off work, manage thread attention, and schedule "
            "persistent reminders."
        ),
        epilog=(
            "Run `bcc <resource> --help` or "
            "`bcc <resource> <command> --help` for command-specific usage."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="resource",
        required=True,
        metavar="{message,inbox,handoff,thread,reminder}",
        title="resources",
    )

    message_parser = subparsers.add_parser(
        "message",
        help="Message operations",
        description="Message operations",
    )
    message_subparsers = message_parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{check,read,send}",
        title="message commands",
    )
    message_subparsers.add_parser(
        "check",
        help="Drain the agent inbox (non-blocking). Acks delivered seqs before returning.",
        description="Drain the agent inbox (non-blocking). Acks delivered seqs before returning.",
    )

    read_parser = message_subparsers.add_parser(
        "read",
        help="Read message history for a channel, DM, or thread",
        description="Read message history for a channel, DM, or thread",
    )
    read_parser.add_argument(
        "--target",
        required=True,
        metavar="<target>",
        help="DM/thread target to read, as shown by `bcc message check`.",
    )
    read_parser.add_argument(
        "--around",
        metavar="<message-id>",
        help="Center the history window around this local message id.",
    )
    read_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        metavar="<n>",
        help="Maximum number of history messages to return (default: 100).",
    )

    inbox_parser = subparsers.add_parser(
        "inbox",
        help="Inbox discovery operations",
        description="Inbox discovery operations",
    )
    inbox_subparsers = inbox_parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{list}",
        title="inbox commands",
    )
    inbox_list_parser = inbox_subparsers.add_parser(
        "list",
        help="List available message targets",
        description="List available message targets",
    )
    inbox_list_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        metavar="<n>",
        help="Maximum number of targets to return (default: 100).",
    )
    inbox_list_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        metavar="<n>",
        help="Number of targets to skip (default: 0).",
    )

    send_parser = message_subparsers.add_parser(
        "send",
        help="Send a reply after the session fresh-check gate.",
        description=(
            "Send a message through the current Channel. The message body is read "
            "from stdin. A recent `bcc message check` or `bcc message read` snapshot "
            "is required before delivery."
        ),
    )
    send_parser.add_argument(
        "--target",
        required=True,
        metavar="<target>",
        help="DM/thread target to reply to.",
    )
    send_parser.add_argument(
        "--reply-to",
        metavar="<message-id>",
        help="Optional local message id to reply to within the target.",
    )
    send_parser.add_argument(
        "--attachment",
        action="append",
        default=[],
        metavar="<path>",
        help="Workspace file to attach; repeat the option for multiple files.",
    )
    send_parser.add_argument(
        "--send-draft",
        action="store_true",
        help="Send this target's active draft unchanged after rechecking freshness.",
    )

    thread_parser = subparsers.add_parser(
        "thread",
        help="Thread attention operations",
        description="Thread attention operations",
    )
    thread_subparsers = thread_parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{unfollow}",
        title="thread commands",
    )
    unfollow_parser = thread_subparsers.add_parser(
        "unfollow",
        help="Stop following a group/thread target.",
        description=(
            "Stop following the current group/thread target for future message wakes. "
            "This does not affect Reminder ownership or Reminder wakes."
        ),
    )
    unfollow_parser.add_argument(
        "--target",
        required=True,
        metavar="<target>",
        help="Group/thread target to unfollow.",
    )

    handoff_parser = subparsers.add_parser(
        "handoff",
        help="Cross-session handoff operations",
        description="Cross-session handoff operations",
    )
    handoff_subparsers = handoff_parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{send,check}",
        title="handoff commands",
    )
    handoff_send_parser = handoff_subparsers.add_parser(
        "send",
        help="Send a handoff to another conversation owned by this agent.",
        description=(
            "Persist a handoff for another conversation owned by this agent and "
            "wake its runtime. The handoff body is read from stdin."
        ),
    )
    handoff_send_parser.add_argument(
        "--target",
        required=True,
        metavar="<target>",
        help="Target conversation, as shown by `bcc inbox list`.",
    )
    handoff_send_parser.add_argument(
        "--message-id",
        metavar="<message-id>",
        help="Optional inbound message in the current session used as source context.",
    )
    handoff_subparsers.add_parser(
        "check",
        help="Drain pending handoffs.",
        description=(
            "Read up to 100 pending handoffs for the current session and mark "
            "exactly the returned handoffs as read."
        ),
    )

    reminder_parser = subparsers.add_parser(
        "reminder",
        help="Reminder operations",
        description="Reminder operations",
    )
    reminder_subparsers = reminder_parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{schedule,check,list,snooze,update,cancel}",
        title="reminder commands",
    )

    schedule_parser = reminder_subparsers.add_parser(
        "schedule",
        help="Schedule a one-time or recurring Reminder.",
        description=(
            "Schedule a persistent Reminder anchored to an inbound message in the "
            "current session. --title and --message-id are required. "
            "Provide at least one of --delay-seconds, --fire-at, or --repeat; "
            "--delay-seconds and --fire-at cannot be combined."
        ),
        epilog=(
            "Recurrence rules: every:15m | every:2h | every:1d | daily@09:00 | "
            "weekly:mon,fri@09:00. Use --tz with calendar recurrences when a specific "
            "IANA timezone is required."
        ),
    )
    schedule_parser.add_argument(
        "--title",
        metavar="<t>",
        help="Required short description of what the Reminder is about.",
    )
    schedule_parser.add_argument(
        "--delay-seconds",
        type=int,
        metavar="<n>",
        help="Fire this many seconds from command evaluation time.",
    )
    schedule_parser.add_argument(
        "--fire-at",
        metavar="<iso>",
        help="Absolute ISO-8601 timestamp for the first fire.",
    )
    schedule_parser.add_argument(
        "--repeat",
        metavar="<rule>",
        help="Optional recurrence rule; see supported grammar below.",
    )
    schedule_parser.add_argument(
        "--tz",
        metavar="<iana>",
        help="IANA timezone for calendar recurrence, for example Asia/Shanghai.",
    )
    schedule_parser.add_argument(
        "--message-id",
        metavar="<id>",
        help="Required full uuid for the local inbound message used as anchor.",
    )

    reminder_subparsers.add_parser(
        "check",
        help="Drain pending Reminder occurrences.",
        description=(
            "Read up to 100 pending Reminder occurrences for the current session and "
            "mark exactly the returned occurrences as read. Use this after a Reminder "
            "notice. A read marker means the occurrence was inspected, not that its "
            "business task was completed."
        ),
    )

    list_parser = reminder_subparsers.add_parser(
        "list",
        help="List your own reminders (defaults to scheduled and fired)",
        description="List your own reminders (defaults to scheduled and fired)",
    )
    list_parser.add_argument(
        "--all",
        action="store_true",
        help="Include canceled reminders",
    )
    list_parser.add_argument(
        "--status",
        metavar="<scheduled,fired,canceled>",
        help="Comma-separated statuses (scheduled,fired,canceled). Default: scheduled,fired",
    )

    snooze_parser = reminder_subparsers.add_parser(
        "snooze",
        help="Snooze a scheduled or fired reminder",
        description="Snooze a scheduled or fired reminder",
    )
    snooze_parser.add_argument(
        "--id",
        dest="reminder_id",
        metavar="<id>",
        help="Reminder id (full uuid)",
    )
    snooze_parser.add_argument(
        "--by",
        metavar="<duration>",
        help="Snooze duration, e.g. 30m, 2h, 1d",
    )

    update_parser = reminder_subparsers.add_parser(
        "update",
        help="Update one field on a scheduled reminder",
        description="Update one field on a scheduled reminder",
    )
    update_parser.add_argument(
        "--id",
        dest="reminder_id",
        metavar="<id>",
        help="Reminder id (full uuid)",
    )
    update_parser.add_argument(
        "--fire-at",
        metavar="<iso>",
        help="New absolute next fire time",
    )
    update_parser.add_argument(
        "--in",
        dest="in_duration",
        metavar="<duration>",
        help="New relative next fire time, e.g. 30m, 2h",
    )
    update_parser.add_argument(
        "--cadence",
        metavar="<rule>",
        help="New recurrence rule: every:15m | daily@09:00 | weekly:mon,fri@09:00",
    )
    update_parser.add_argument(
        "--title",
        metavar="<text>",
        help="New reminder title",
    )

    cancel_parser = reminder_subparsers.add_parser(
        "cancel",
        help="Cancel a scheduled reminder by id (full uuid)",
        description="Cancel a scheduled reminder by id (full uuid)",
    )
    cancel_parser.add_argument(
        "--id",
        dest="reminder_id",
        metavar="<id>",
        help="Reminder id (full uuid)",
    )
    return parser


async def _request(
    args: argparse.Namespace,
    *,
    body: str | None = None,
) -> Mapping[str, object]:
    endpoint = os.environ.get("BCN_ENDPOINT")
    session_id = os.environ.get("BCN_SESSION_ID")
    if not endpoint:
        raise BccCommandError(
            "BCN_ENDPOINT is not set",
            code="LOCAL_ENDPOINT_REQUIRED",
        )
    if not session_id:
        raise BccCommandError(
            "BCN_SESSION_ID is not set",
            code="SESSION_REQUIRED",
        )
    runtime_session_id = os.environ.get("BCN_RUNTIME_SESSION_ID")
    if not runtime_session_id:
        raise BccCommandError(
            "BCN_RUNTIME_SESSION_ID is not set",
            code="SESSION_BINDING_REQUIRED",
        )
    session_capability = os.environ.get("BCN_COMMAND_CAPABILITY")
    if not session_capability:
        raise BccCommandError(
            "BCN_COMMAND_CAPABILITY is not set",
            code="SESSION_BINDING_REQUIRED",
        )

    request: dict[str, object] = {
        "kind": "command",
        "resource": args.resource,
        "session_id": session_id,
        "runtime_session_id": runtime_session_id,
        "session_capability": session_capability,
        "command": args.command,
    }
    if args.resource == "message":
        if args.command == "read":
            request["target"] = args.target
            request["around_message_id"] = args.around
            request["limit"] = args.limit
        elif args.command == "send":
            if args.send_draft and (args.reply_to is not None or args.attachment):
                raise BccCommandError(
                    "--send-draft cannot be combined with --reply-to or --attachment",
                    code="INVALID_SEND_DRAFT",
                )
            request["target"] = args.target
            request["body"] = body if body is not None else ""
            request["command_id"] = f"bcc-{uuid7().hex}"
            request["reply_to_message_id"] = args.reply_to
            request["send_draft"] = args.send_draft
            request["attachment_paths"] = await asyncio.to_thread(
                lambda: [str(Path(path).absolute()) for path in args.attachment]
            )
    elif args.resource == "inbox":
        if args.command == "list":
            request["limit"] = args.limit
            request["offset"] = args.offset
    elif args.resource == "thread":
        request["target"] = args.target
    elif args.resource == "handoff":
        if args.command == "send":
            request.update(
                {
                    "target": args.target,
                    "body": body if body is not None else "",
                    "command_id": f"bcc-{uuid7().hex}",
                    "source_message_id": args.message_id,
                }
            )
    elif args.resource == "reminder":
        if args.command == "schedule":
            request.update(
                {
                    "title": args.title,
                    "delay_seconds": args.delay_seconds,
                    "fire_at": args.fire_at,
                    "repeat_rule": args.repeat,
                    "timezone": args.tz,
                    "message_id": args.message_id,
                }
            )
        elif args.command == "list":
            request["all"] = args.all
            request["status"] = args.status
        elif args.command == "snooze":
            request["reminder_id"] = args.reminder_id
            request["by"] = args.by
        elif args.command == "update":
            request.update(
                {
                    "reminder_id": args.reminder_id,
                    "fire_at": args.fire_at,
                    "in": args.in_duration,
                    "cadence": args.cadence,
                    "title": args.title,
                }
            )
        elif args.command == "cancel":
            request["reminder_id"] = args.reminder_id

    response = await LocalCommandClient.request(endpoint, request)
    if response.get("ok") is not True:
        raise BccCommandError(
            str(response.get("error", "command failed")),
            code=str(response.get("code", "COMMAND_FAILED")),
            draft_saved=response.get("draft_saved") is True,
            next_action=(
                str(response["next_action"])
                if response.get("next_action") is not None
                else None
            ),
        )
    return response


def _result(response: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], response["result"])


def _format_inbox_sender(value: object) -> str:
    if value is None:
        return "none"
    sender = cast(Mapping[str, object], value)
    sender_id = cast(str | None, sender.get("id"))
    sender_name = cast(str | None, sender.get("name"))
    return f"@{sender_name or sender_id}"


def _format_inbox_target(summary: Mapping[str, object]) -> str:
    target = cast(str, summary["target"])
    session_id = cast(str, summary["session_id"])
    target_kind = cast(str, summary["target_kind"])
    current = cast(bool, summary["current"])
    pending_count = cast(int, summary["pending_count"])
    latest_message_id = cast(str | None, summary["latest_message_id"])
    latest_sender = summary.get("latest_sender")
    latest_time_ms = cast(int | None, summary["latest_time_ms"])
    if latest_message_id is None:
        latest_message_text = "none"
        latest_sender_text = "none"
        latest_time_text = "none"
    else:
        latest_message_text = latest_message_id
        latest_sender_text = _format_inbox_sender(latest_sender)
        latest_time_text = format_message_time(cast(int, latest_time_ms))

    return (
        f"[target={target} session={session_id} kind={target_kind} "
        f"current={str(current).lower()} pending={pending_count} "
        f"latest-msg={latest_message_text} latest-sender={latest_sender_text} "
        f"latest-time={latest_time_text}]"
    )


def serialize_inbox_list(result: Mapping[str, object]) -> str:
    targets = cast(list[Mapping[str, object]], result["targets"])
    total = cast(int, result["total"])
    shown = cast(int, result["shown"])
    offset = cast(int, result["offset"])
    has_more = cast(bool, result["has_more"])

    rendered_targets: list[str] = []
    for target in targets:
        rendered_targets.append(_format_inbox_target(target))

    lines = [
        (
            f"Inbox targets: {shown} returned, offset {offset}, total {total}, "
            "ordered by recent activity."
        )
    ]
    lines.extend(rendered_targets)
    if total == 0:
        lines.append("No message targets.")
    elif not targets:
        lines.append("No more message targets.")
    elif has_more:
        lines.append(
            f"More message targets remain. Run `bcc inbox list --offset {offset + shown}`."
        )
    else:
        lines.append("No more message targets.")
    return "\n".join(lines)


def serialize_check(result: Mapping[str, object]) -> str:
    messages = cast(list[Mapping[str, object]], result["messages"])
    referenced_messages = cast(
        list[Mapping[str, object]], result["referenced_messages"]
    )
    lines: list[str] = []
    if referenced_messages:
        lines.append(f"Referenced messages: {len(referenced_messages)}")
        lines.extend(
            format_read_message(
                message,
                index=index,
                count=len(referenced_messages),
            )
            for index, message in enumerate(referenced_messages, start=1)
        )
        lines.append("New messages:")
    lines.extend(format_check_message(message) for message in messages)
    if not lines:
        lines.append("No more new messages.")
    return "\n".join(lines)


def serialize_read(result: Mapping[str, object]) -> str:
    messages = cast(list[Mapping[str, object]], result["messages"])
    referenced_messages = cast(
        list[Mapping[str, object]], result["referenced_messages"]
    )
    first_seq = result["first_seq"]
    last_seq = result["last_seq"]
    if not messages:
        bounds = "none-none"
    else:
        bounds = f"{cast(int, first_seq)}-{cast(int, last_seq)}"
    lines = [f"Read window: {len(messages)} returned, seq {bounds}, oldest to newest."]
    if referenced_messages:
        lines.append(f"Referenced messages: {len(referenced_messages)}")
        lines.extend(
            format_read_message(
                message,
                index=index,
                count=len(referenced_messages),
            )
            for index, message in enumerate(referenced_messages, start=1)
        )
        lines.append("Window messages:")
    lines.extend(
        format_read_message(message, index=index, count=len(messages))
        for index, message in enumerate(messages, start=1)
    )
    return "\n".join(lines)


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


def _reminder(result: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], result["reminder"])


def _reminder_label(reminder: Mapping[str, object]) -> str:
    repeat_rule = reminder["repeat_rule"]
    if repeat_rule is None:
        return "one-time"
    return cast(str, repeat_rule)


def _quoted_title(reminder: Mapping[str, object]) -> str:
    return json.dumps(cast(str, reminder["title"]), ensure_ascii=False)


def serialize_reminder_schedule(result: Mapping[str, object]) -> str:
    reminder = _reminder(result)
    reminder_id = cast(str, reminder["reminder_id"])
    next_fire = cast(int, reminder["next_fire_at_ms"])
    return (
        f"Reminder scheduled: #{reminder_id} ({_reminder_label(reminder)}) "
        f"{_quoted_title(reminder)}\nNext: {format_utc_timestamp(next_fire)}"
    )


def serialize_reminder_check(result: Mapping[str, object]) -> str:
    items = cast(list[Mapping[str, object]], result["items"])
    has_more = cast(bool, result["has_more"])
    if not items:
        return "No pending reminders."

    lines: list[str] = []
    for item in items:
        occurrence = cast(Mapping[str, object], item["occurrence"])
        reminder_id = cast(str, occurrence["reminder_id"])
        occurrence_no = cast(int, occurrence["occurrence_no"])
        scheduled = format_utc_timestamp(cast(int, occurrence["scheduled_for_ms"]))
        fired = format_utc_timestamp(cast(int, occurrence["fired_at_ms"]))
        overdue = cast(bool, occurrence["overdue"])
        next_fire_at_ms = cast(int | None, occurrence["next_fire_at_ms"])
        next_text = (
            format_utc_timestamp(next_fire_at_ms)
            if next_fire_at_ms is not None
            else "none"
        )
        target = cast(str, item["canonical_target"])
        anchor = cast(str, occurrence["anchor_message_id"])
        title = cast(str, item["title"])
        lines.append(
            f"[class=due id={reminder_id} occurrence={occurrence_no} "
            f"scheduled={scheduled} fired={fired} "
            f"overdue={str(overdue).lower()} next={next_text} "
            f"target={target} anchor={anchor}] {title}"
        )
    lines.append(
        "More pending reminders remain. Run `bcc reminder check` again."
        if has_more
        else "No more pending reminders."
    )
    return "\n".join(lines)


def serialize_reminder_list(result: Mapping[str, object]) -> str:
    reminders = cast(list[Mapping[str, object]], result["reminders"])
    if not reminders:
        return "No reminders."
    lines: list[str] = []
    for reminder in reminders:
        reminder_id = cast(str, reminder["reminder_id"])
        anchor = cast(str, reminder["anchor_message_id"])
        state = cast(str, reminder["state"])
        title = _quoted_title(reminder)
        label = _reminder_label(reminder)
        if state == "scheduled":
            timestamp = cast(int, reminder["next_fire_at_ms"])
            lines.append(
                f"#{reminder_id} [scheduled] ({label}) "
                f"next={format_utc_timestamp(timestamp)} {title} anchor={anchor}"
            )
        elif state == "fired":
            timestamp = cast(int, reminder["last_fired_at_ms"])
            lines.append(
                f"#{reminder_id} [fired] (one-time) "
                f"fired_at={format_utc_timestamp(timestamp)} {title} anchor={anchor}"
            )
        elif state == "canceled":
            timestamp = cast(int, reminder["canceled_at_ms"])
            lines.append(
                f"#{reminder_id} [canceled] ({label}) "
                f"canceled_at={format_utc_timestamp(timestamp)} {title} anchor={anchor}"
            )
    return "\n".join(lines)


def _serialize_reminder_mutation(
    result: Mapping[str, object],
    *,
    verb: str,
    include_next: bool,
) -> str:
    reminder = _reminder(result)
    reminder_id = cast(str, reminder["reminder_id"])
    line = f"Reminder {verb}: #{reminder_id}"
    if not include_next:
        return line
    next_fire = cast(int, reminder["next_fire_at_ms"])
    return f"{line}\nNext: {format_utc_timestamp(next_fire)}"


def serialize_reminder_snooze(result: Mapping[str, object]) -> str:
    return _serialize_reminder_mutation(result, verb="snoozed", include_next=True)


def serialize_reminder_update(result: Mapping[str, object]) -> str:
    return _serialize_reminder_mutation(result, verb="updated", include_next=True)


def serialize_reminder_cancel(result: Mapping[str, object]) -> str:
    return _serialize_reminder_mutation(result, verb="canceled", include_next=False)


HandoffText = Annotated[StrictStr, Field(min_length=1)]
HandoffTime = Annotated[StrictInt, Field(ge=0)]


class _HandoffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    handoff_id: HandoffText
    command_id: HandoffText
    source_session_id: HandoffText
    target_session_id: HandoffText
    source_message_id: HandoffText | None
    body: StrictStr
    created_at_ms: HandoffTime
    read_at_ms: HandoffTime | None

    @field_validator("body")
    @classmethod
    def _require_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("handoff body must contain non-whitespace text")
        return value

    @field_validator("source_message_id")
    @classmethod
    def _require_source_message_id(cls, value: str | None) -> str | None:
        return None if value is None else canonical_id_reference(value)


class _HandoffSendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    handoff: _HandoffResponse
    target: HandoffText

    @field_validator("handoff")
    @classmethod
    def _require_pending_handoff(cls, value: _HandoffResponse) -> _HandoffResponse:
        if value.read_at_ms is not None:
            raise ValueError("sent handoff must not contain read_at_ms")
        return value


class _HandoffCheckItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    handoff: _HandoffResponse
    source_target: HandoffText

    @field_validator("handoff")
    @classmethod
    def _require_read_handoff(cls, value: _HandoffResponse) -> _HandoffResponse:
        if value.read_at_ms is None:
            raise ValueError("checked handoff must contain read_at_ms")
        return value


class _HandoffCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[_HandoffCheckItemResponse]
    has_more: StrictBool


def _validate_handoff_response[ResponseT: BaseModel](
    result: Mapping[str, object], model: type[ResponseT]
) -> ResponseT:
    try:
        return model.model_validate(result)
    except ValidationError as error:
        raise BccCommandError(
            "Handoff command returned an invalid response.",
            code="HANDOFF_RESPONSE_INVALID",
        ) from error


def serialize_handoff_send(result: Mapping[str, object]) -> str:
    response = _validate_handoff_response(result, _HandoffSendResponse)
    return f"Handoff sent: #{response.handoff.handoff_id} target={response.target}"


def serialize_handoff_check(result: Mapping[str, object]) -> str:
    response = _validate_handoff_response(result, _HandoffCheckResponse)
    if not response.items:
        return "No pending handoffs."

    lines = []
    for item in response.items:
        handoff = item.handoff
        source_message = handoff.source_message_id or "none"
        lines.append(
            f"[handoff={handoff.handoff_id} source={item.source_target} "
            f"message={source_message} "
            f"time={format_message_time(handoff.created_at_ms)}] {handoff.body}"
        )
    lines.append(
        "More pending handoffs remain. Run `bcc handoff check` again."
        if response.has_more
        else "No more pending handoffs."
    )
    return "\n".join(lines)


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    body = None
    if (
        args.resource in {"message", "handoff"}
        and args.command == "send"
        and not getattr(args, "send_draft", False)
    ):
        body = await asyncio.to_thread(sys.stdin.read)
    response = await _request(args, body=body)
    result = _result(response)

    if args.resource == "message":
        if args.command == "check":
            print(serialize_check(result))
        elif args.command == "read":
            print(serialize_read(result))
        elif args.command == "send":
            print(serialize_send(result))
    elif args.resource == "inbox":
        if args.command == "list":
            print(serialize_inbox_list(result))
    elif args.resource == "handoff":
        serializer = {
            "send": serialize_handoff_send,
            "check": serialize_handoff_check,
        }[args.command]
        print(serializer(result))
    elif args.resource == "reminder":
        serializer = {
            "schedule": serialize_reminder_schedule,
            "check": serialize_reminder_check,
            "list": serialize_reminder_list,
            "snooze": serialize_reminder_snooze,
            "update": serialize_reminder_update,
            "cancel": serialize_reminder_cancel,
        }[args.command]
        print(serializer(result))
    return 0


def _print_error(error: BccCommandError) -> NoReturn:
    print(f"Error: {error.message}", file=sys.stderr)
    print(f"Code: {error.code}", file=sys.stderr)
    if error.draft_saved:
        print("Draft saved: yes", file=sys.stderr)
    if error.next_action is not None:
        print(f"Next action: {error.next_action}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except BccCommandError as error:
        _print_error(error)


if __name__ == "__main__":
    raise SystemExit(main())
