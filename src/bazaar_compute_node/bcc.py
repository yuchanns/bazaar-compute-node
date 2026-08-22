from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn
from uuid import uuid7

from .app.command import format_message_time
from .app.transport import LocalCommandClient
from .core.reminder import format_utc_timestamp


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
            "send replies, manage thread attention, and schedule persistent reminders."
        ),
        epilog=(
            "Run `bcc <resource> --help` or "
            "`bcc <resource> <command> --help` for command-specific usage."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="resource",
        required=True,
        metavar="{message,thread,reminder}",
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
            request["target"] = args.target
            request["body"] = body if body is not None else ""
            request["command_id"] = f"bcc-{uuid7().hex}"
            request["reply_to_message_id"] = args.reply_to
            request["attachment_paths"] = await asyncio.to_thread(
                lambda: [str(Path(path).absolute()) for path in args.attachment]
            )
    elif args.resource == "thread":
        request["target"] = args.target
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


def _require_result(response: Mapping[str, object]) -> Mapping[str, object]:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise BccCommandError(
            "command response has no result object",
            code="INVALID_RESPONSE",
        )
    return result


def _invalid_response(message: str) -> NoReturn:
    raise BccCommandError(message, code="INVALID_RESPONSE")


def _require_text(
    value: Mapping[str, object],
    field_name: str,
    *,
    allow_empty: bool = False,
) -> str:
    item = value.get(field_name)
    if not isinstance(item, str) or (not allow_empty and not item):
        _invalid_response(f"command response contains an invalid {field_name}")
    return item


def _require_non_negative_int(
    value: Mapping[str, object],
    field_name: str,
) -> int:
    item = value.get(field_name)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        _invalid_response(f"command response contains an invalid {field_name}")
    return item


def _optional_non_negative_int(
    value: Mapping[str, object],
    field_name: str,
) -> int | None:
    item = value.get(field_name)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        _invalid_response(f"command response contains an invalid {field_name}")
    return item


def _require_result_sequence(result: Mapping[str, object], field_name: str) -> int:
    return _require_non_negative_int(result, field_name)


def _require_message_list(
    result: Mapping[str, object], field_name: str
) -> list[Mapping[str, object]]:
    messages = result.get(field_name)
    if not isinstance(messages, list):
        _invalid_response(f"command response has no {field_name} list")
    if not all(isinstance(message, Mapping) for message in messages):
        _invalid_response("command response contains an invalid message")
    return messages


def _require_messages(result: Mapping[str, object]) -> list[Mapping[str, object]]:
    return _require_message_list(result, "messages")


def _message_timestamp(message: Mapping[str, object]) -> int:
    timestamp = message.get("provider_time_ms")
    if timestamp is None:
        timestamp = message.get("received_at_ms")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        _invalid_response("command response contains an invalid message timestamp")
    return timestamp


def _format_message_timestamp(message: Mapping[str, object]) -> str:
    return format_message_time(_message_timestamp(message))


def _message_header_fields(
    message: Mapping[str, object],
) -> tuple[str, str, str, str, str | None, str]:
    target = _require_text(message, "canonical_target")
    message_id = _require_text(message, "message_id")
    sender_kind = _require_text(message, "sender_kind")
    if sender_kind not in {"human", "agent", "unknown"}:
        _invalid_response("command response contains an invalid sender_kind")
    sender_value = message.get("sender")
    sender: str | None = None
    if sender_value is not None:
        if not isinstance(sender_value, Mapping):
            _invalid_response("command response contains an invalid message sender")
        else:
            sender_id = sender_value.get("id")
            sender_name = sender_value.get("name")
            sender = f"@{sender_id}({sender_name})" if sender_name else f"@{sender_id}"
    return (
        target,
        message_id,
        _format_message_timestamp(message),
        sender_kind,
        sender,
        _require_text(message, "body", allow_empty=True),
    )


def _attachment_suffix(message: Mapping[str, object]) -> str:
    attachments = message.get("attachments")
    if not isinstance(attachments, list):
        _invalid_response("command response contains invalid attachments")
    if not attachments:
        return ""
    rendered: list[str] = []
    for attachment in attachments:
        if not isinstance(attachment, Mapping):
            _invalid_response("command response contains an invalid attachment")
        name = _require_text(attachment, "name")
        attachment_id = _require_text(attachment, "attachment_id")
        state = _require_text(attachment, "state")
        if state == "ready":
            path = _require_text(attachment, "relative_path")
            rendered.append(f"{name} (id:{attachment_id}, path:{path})")
        elif state == "failed":
            error = _require_text(attachment, "error")
            rendered.append(f"{name} (id:{attachment_id}, state:failed, error:{error})")
        else:
            _invalid_response("command response contains an invalid attachment state")
    label = "attachment" if len(rendered) == 1 else "attachments"
    return f" [{len(rendered)} {label}: {', '.join(rendered)}]"


def _format_check_message(message: Mapping[str, object]) -> str:
    target, message_id, timestamp, message_type, sender, body = _message_header_fields(
        message
    )
    line = (
        f"[target={target} msg={message_id} time={timestamp} "
        f"type={message_type} mentioned={str(message.get('mentions_agent') is True).lower()}"
    )
    reply_to_message_id = message.get("reply_to_message_id")
    if reply_to_message_id is not None:
        if not isinstance(reply_to_message_id, str) or not reply_to_message_id:
            _invalid_response(
                "command response contains an invalid message reply_to_message_id"
            )
        line += f" reply_to={reply_to_message_id}"
    line += "] "
    if sender is not None:
        line += f"{sender} "
    return line + body + _attachment_suffix(message)


def _format_read_message(
    message: Mapping[str, object],
    *,
    index: int,
    count: int,
) -> str:
    target, message_id, timestamp, message_type, sender, body = _message_header_fields(
        message
    )
    fields = [
        f"seq={_require_non_negative_int(message, 'seq')}",
        f"msg={message_id}",
        f"time={timestamp}",
        f"type={message_type}",
        f"replyTarget={target}",
        f"mentioned={str(message.get('mentions_agent') is True).lower()}",
    ]
    reply_to_message_id = message.get("reply_to_message_id")
    if reply_to_message_id is not None:
        if not isinstance(reply_to_message_id, str) or not reply_to_message_id:
            _invalid_response(
                "command response contains an invalid message reply_to_message_id"
            )
        fields.append(f"replyTo={reply_to_message_id}")
    line = f"[{index}/{count} {' '.join(fields)}] "
    if sender is not None:
        line += f"{sender} "
    return line + body + _attachment_suffix(message)


def serialize_check(result: Mapping[str, object]) -> str:
    snapshot_seq = _require_result_sequence(result, "snapshot_seq")
    delivered_through_seq = _require_result_sequence(result, "delivered_through_seq")
    if delivered_through_seq > snapshot_seq:
        _invalid_response(
            "command response contains an invalid check sequence boundary"
        )
    messages = _require_messages(result)
    referenced_messages = _require_message_list(result, "referenced_messages")
    lines: list[str] = []
    if referenced_messages:
        lines.append(f"Referenced messages: {len(referenced_messages)}")
        lines.extend(
            _format_read_message(
                message,
                index=index,
                count=len(referenced_messages),
            )
            for index, message in enumerate(referenced_messages, start=1)
        )
        lines.append("New messages:")
    lines.extend(_format_check_message(message) for message in messages)
    if not lines:
        lines.append("No more new messages.")
    return "\n".join(lines)


def serialize_read(result: Mapping[str, object]) -> str:
    _require_result_sequence(result, "snapshot_seq")
    messages = _require_messages(result)
    referenced_messages = _require_message_list(result, "referenced_messages")
    first_seq = result.get("first_seq")
    last_seq = result.get("last_seq")
    if not messages:
        if first_seq is not None or last_seq is not None:
            _invalid_response("empty read response has sequence bounds")
        bounds = "none-none"
    else:
        if (
            isinstance(first_seq, bool)
            or not isinstance(first_seq, int)
            or first_seq < 0
            or isinstance(last_seq, bool)
            or not isinstance(last_seq, int)
            or last_seq < first_seq
        ):
            _invalid_response("command response contains invalid read bounds")
        if first_seq != _require_non_negative_int(
            messages[0], "seq"
        ) or last_seq != _require_non_negative_int(messages[-1], "seq"):
            _invalid_response("command response read bounds do not match messages")
        bounds = f"{first_seq}-{last_seq}"
    lines = [f"Read window: {len(messages)} returned, seq {bounds}, oldest to newest."]
    if referenced_messages:
        lines.append(f"Referenced messages: {len(referenced_messages)}")
        lines.extend(
            _format_read_message(
                message,
                index=index,
                count=len(referenced_messages),
            )
            for index, message in enumerate(referenced_messages, start=1)
        )
        lines.append("Window messages:")
    lines.extend(
        _format_read_message(message, index=index, count=len(messages))
        for index, message in enumerate(messages, start=1)
    )
    return "\n".join(lines)


def serialize_send(result: Mapping[str, object]) -> str:
    outbound = result.get("outbound")
    if not isinstance(outbound, Mapping):
        _invalid_response("command response has no outbound object")
    state = outbound.get("state")
    if not isinstance(state, str) or state not in {
        "sent",
        "queued",
        "partial",
        "failed",
        "unknown",
        "rejected",
    }:
        _invalid_response("command response contains an invalid outbound state")
    target = _require_text(outbound, "target")
    outbound_message_id = _require_text(outbound, "outbound_message_id")
    if state == "sent":
        return f"Message sent to {target}. Message ID: {outbound_message_id}"
    if state == "queued":
        return f"Message queued to {target}. Message ID: {outbound_message_id}"

    error_kind = outbound.get("error_kind")
    error_message = outbound.get("error_message")
    if not isinstance(error_kind, str) or not error_kind:
        _invalid_response("command response contains no outbound error kind")
    if not isinstance(error_message, str) or not error_message:
        _invalid_response("command response contains no outbound error message")
    draft_saved_at_ms = _optional_non_negative_int(outbound, "draft_saved_at_ms")
    if state == "rejected" and draft_saved_at_ms is None and error_kind != "empty_body":
        _invalid_response("rejected outbound response has no saved draft")
    next_action_value = outbound.get("next_action")
    if next_action_value is not None and (
        not isinstance(next_action_value, str) or not next_action_value
    ):
        _invalid_response("command response contains an invalid outbound next_action")
    next_action = next_action_value if isinstance(next_action_value, str) else None
    if state == "partial":
        code = "SEND_PARTIAL"
    elif state == "unknown" or error_kind == "provider_unknown":
        code = "SEND_UNKNOWN"
    elif error_kind == "empty_body":
        code = "SEND_EMPTY_BODY"
    elif error_kind == "fresh_check_required":
        code = "SEND_FRESH_CHECK_REQUIRED"
    elif error_kind == "fresh_check_failed":
        code = "SEND_FRESH_CHECK_FAILED"
    elif state == "failed" or error_kind in {"provider_failed", "target_not_replyable"}:
        code = "SEND_FAILED"
    else:
        code = "SEND_REJECTED"
    raise BccCommandError(
        error_message,
        code=code,
        draft_saved=draft_saved_at_ms is not None,
        next_action=next_action,
    )


def _require_reminder(result: Mapping[str, object]) -> Mapping[str, object]:
    reminder = result.get("reminder")
    if not isinstance(reminder, Mapping):
        _invalid_response("command response has no reminder object")
    return reminder


def _reminder_label(reminder: Mapping[str, object]) -> str:
    repeat_rule = reminder.get("repeat_rule")
    if repeat_rule is None:
        return "one-time"
    if not isinstance(repeat_rule, str) or not repeat_rule:
        _invalid_response("command response contains an invalid repeat_rule")
    return repeat_rule


def _quoted_title(reminder: Mapping[str, object]) -> str:
    return json.dumps(_require_text(reminder, "title"), ensure_ascii=False)


def serialize_reminder_schedule(result: Mapping[str, object]) -> str:
    reminder = _require_reminder(result)
    reminder_id = _require_text(reminder, "reminder_id")
    next_fire = _optional_non_negative_int(reminder, "next_fire_at_ms")
    if next_fire is None:
        _invalid_response("scheduled Reminder response has no next fire time")
    return (
        f"Reminder scheduled: #{reminder_id} ({_reminder_label(reminder)}) "
        f"{_quoted_title(reminder)}\nNext: {format_utc_timestamp(next_fire)}"
    )


def serialize_reminder_check(result: Mapping[str, object]) -> str:
    items = result.get("items")
    has_more = result.get("has_more")
    if not isinstance(items, list) or not all(
        isinstance(item, Mapping) for item in items
    ):
        _invalid_response("command response contains invalid Reminder check items")
    if not isinstance(has_more, bool):
        _invalid_response("command response contains invalid Reminder check has_more")
    if not items:
        return "No pending reminders."

    lines: list[str] = []
    for item in items:
        occurrence = item.get("occurrence")
        if not isinstance(occurrence, Mapping):
            _invalid_response("Reminder check item has no occurrence")
        reminder_id = _require_text(occurrence, "reminder_id")
        occurrence_no = _require_non_negative_int(occurrence, "occurrence_no")
        if occurrence_no <= 0:
            _invalid_response("Reminder occurrence number must be positive")
        scheduled = format_utc_timestamp(
            _require_non_negative_int(occurrence, "scheduled_for_ms")
        )
        fired = format_utc_timestamp(
            _require_non_negative_int(occurrence, "fired_at_ms")
        )
        overdue = occurrence.get("overdue")
        if not isinstance(overdue, bool):
            _invalid_response("Reminder occurrence overdue must be boolean")
        next_fire_at_ms = _optional_non_negative_int(occurrence, "next_fire_at_ms")
        next_text = (
            format_utc_timestamp(next_fire_at_ms)
            if next_fire_at_ms is not None
            else "none"
        )
        target = _require_text(item, "canonical_target")
        anchor = _require_text(occurrence, "anchor_message_id")
        title = _require_text(item, "title")
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
    reminders = result.get("reminders")
    if not isinstance(reminders, list) or not all(
        isinstance(reminder, Mapping) for reminder in reminders
    ):
        _invalid_response("command response contains invalid Reminder list")
    if not reminders:
        return "No reminders."
    lines: list[str] = []
    for reminder in reminders:
        reminder_id = _require_text(reminder, "reminder_id")
        anchor = _require_text(reminder, "anchor_message_id")
        state = _require_text(reminder, "state")
        title = _quoted_title(reminder)
        label = _reminder_label(reminder)
        if state == "scheduled":
            timestamp = _optional_non_negative_int(reminder, "next_fire_at_ms")
            if timestamp is None:
                _invalid_response("scheduled Reminder has no next fire time")
            lines.append(
                f"#{reminder_id} [scheduled] ({label}) "
                f"next={format_utc_timestamp(timestamp)} {title} anchor={anchor}"
            )
        elif state == "fired":
            timestamp = _optional_non_negative_int(reminder, "last_fired_at_ms")
            if timestamp is None:
                _invalid_response("fired Reminder has no fired time")
            lines.append(
                f"#{reminder_id} [fired] (one-time) "
                f"fired_at={format_utc_timestamp(timestamp)} {title} anchor={anchor}"
            )
        elif state == "canceled":
            timestamp = _optional_non_negative_int(reminder, "canceled_at_ms")
            if timestamp is None:
                _invalid_response("canceled Reminder has no canceled time")
            lines.append(
                f"#{reminder_id} [canceled] ({label}) "
                f"canceled_at={format_utc_timestamp(timestamp)} {title} anchor={anchor}"
            )
        else:
            _invalid_response("command response contains an invalid Reminder state")
    return "\n".join(lines)


def _serialize_reminder_mutation(
    result: Mapping[str, object],
    *,
    verb: str,
    include_next: bool,
) -> str:
    reminder = _require_reminder(result)
    reminder_id = _require_text(reminder, "reminder_id")
    line = f"Reminder {verb}: #{reminder_id}"
    if not include_next:
        return line
    next_fire = _optional_non_negative_int(reminder, "next_fire_at_ms")
    if next_fire is None:
        _invalid_response(f"Reminder {verb} response has no next fire time")
    return f"{line}\nNext: {format_utc_timestamp(next_fire)}"


def serialize_reminder_snooze(result: Mapping[str, object]) -> str:
    return _serialize_reminder_mutation(result, verb="snoozed", include_next=True)


def serialize_reminder_update(result: Mapping[str, object]) -> str:
    return _serialize_reminder_mutation(result, verb="updated", include_next=True)


def serialize_reminder_cancel(result: Mapping[str, object]) -> str:
    return _serialize_reminder_mutation(result, verb="canceled", include_next=False)


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    body = None
    if args.resource == "message" and args.command == "send":
        body = await asyncio.to_thread(sys.stdin.read)
    response = await _request(args, body=body)
    result = _require_result(response)

    if args.resource == "message":
        if args.command == "check":
            print(serialize_check(result))
        elif args.command == "read":
            print(serialize_read(result))
        elif args.command == "send":
            print(serialize_send(result))
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
