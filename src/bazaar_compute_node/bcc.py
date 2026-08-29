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
    StrictStr,
    ValidationError,
)

from . import __distribution__
from .app.command import format_check_message, format_message_time, format_read_message
from .app.transport import LocalCommandClient
from .core.reminder import format_utc_timestamp
from .rendering import TextTemplate


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
            "send replies, manage thread attention, schedule "
            "persistent reminders, and upgrade this node."
        ),
        epilog=(
            "Run `bcc <resource> --help` or "
            "`bcc <resource> <command> --help` for command-specific usage."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="resource",
        required=True,
        metavar="{message,inbox,thread,reminder,node}",
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

    reminder_parser = subparsers.add_parser(
        "reminder",
        help="Reminder operations",
        description="Reminder operations",
    )
    reminder_subparsers = reminder_parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{schedule,list,snooze,update,cancel}",
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

    node_parser = subparsers.add_parser(
        "node",
        help="Operations on the node this session runs on",
        description="Operations on the node this session runs on",
    )
    node_subparsers = node_parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{upgrade,version}",
        title="node commands",
    )
    upgrade_parser = node_subparsers.add_parser(
        "upgrade",
        help="Install the newer bcn release and restart this node",
        description=(
            "Install the newer bcn release announced in the inbox notice, schedule a "
            "reminder so you can report the outcome once the node is back, and "
            "restart the node. Run it only after the user agrees to upgrade."
        ),
    )
    upgrade_parser.add_argument(
        "--message-id",
        dest="message_id",
        metavar="<id>",
        help=(
            "Required full uuid for the local inbound message the follow-up reminder "
            "anchors to."
        ),
    )
    node_subparsers.add_parser(
        "version",
        help="Report the version this node is running",
        description=(
            "Report the bcn version of the running node process, which is what "
            "an upgrade has to change to have taken effect."
        ),
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
    if args.resource == "node":
        if args.command == "upgrade":
            request["message_id"] = args.message_id
    elif args.resource == "message":
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

    if args.resource == "node" and args.command == "upgrade":
        # the node installs the release before it answers, and neither side
        # gives up on work the other would carry on doing
        response = await LocalCommandClient.request(endpoint, request, timeout=None)
    else:
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


_INBOX_TARGET = TextTemplate.from_resource("bcc/inbox_target.tpl")
_INBOX_LIST = TextTemplate.from_resource("bcc/inbox_list.tpl")
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
def _result(response: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], response["result"])


def _format_inbox_target(summary: Mapping[str, object]) -> str:
    latest_message_id = cast(str | None, summary["latest_message_id"])
    latest_sender = cast(
        Mapping[str, object] | None,
        summary.get("latest_sender"),
    )
    latest_time_ms = cast(int | None, summary["latest_time_ms"])
    return _INBOX_TARGET.render(
        {
            "target": cast(str, summary["target"]),
            "session_id": cast(str, summary["session_id"]),
            "target_kind": cast(str, summary["target_kind"]),
            "current": str(cast(bool, summary["current"])).lower(),
            "pending_count": cast(int, summary["pending_count"]),
            "latest_message_id": latest_message_id,
            "latest_sender_id": (
                cast(str | None, latest_sender.get("id"))
                if latest_sender is not None
                else None
            ),
            "latest_sender_name": (
                cast(str | None, latest_sender.get("name"))
                if latest_sender is not None
                else None
            ),
            "latest_time": (
                format_message_time(latest_time_ms)
                if latest_time_ms is not None
                else None
            ),
        }
    )


def serialize_inbox_list(result: Mapping[str, object]) -> str:
    targets = cast(list[Mapping[str, object]], result["targets"])
    shown = cast(int, result["shown"])
    offset = cast(int, result["offset"])
    return _INBOX_LIST.render(
        {
            "shown": shown,
            "offset": offset,
            "total": cast(int, result["total"]),
            "has_more": cast(bool, result["has_more"]),
            "next_offset": offset + shown,
            "target_lines": [_format_inbox_target(target) for target in targets],
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
            "reminder_id": cast(str, result["reminder_id"]),
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


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    body = None
    if (
        args.resource == "message"
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
    elif args.resource == "reminder":
        serializer = {
            "schedule": serialize_reminder_schedule,
            "list": serialize_reminder_list,
            "snooze": serialize_reminder_snooze,
            "update": serialize_reminder_update,
            "cancel": serialize_reminder_cancel,
        }[args.command]
        print(serializer(result))
    elif args.resource == "thread" and args.command == "unfollow":
        print(serialize_unfollow(result))
    elif args.resource == "node":
        if args.command == "upgrade":
            print(serialize_upgrade(result))
        elif args.command == "version":
            print(serialize_version(result))
    return 0


def _print_error(error: BccCommandError) -> NoReturn:
    print(
        _ERROR.render(
            {
                "message": error.message,
                "code": error.code,
                "draft_saved": error.draft_saved,
                "next_action": error.next_action,
            }
        ),
        file=sys.stderr,
    )
    raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except BccCommandError as error:
        _print_error(error)


if __name__ == "__main__":
    raise SystemExit(main())
