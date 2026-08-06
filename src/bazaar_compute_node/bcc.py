from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from typing import NoReturn
from uuid import uuid7

from .app.command import format_message_time
from .app.transport import LocalCommandClient


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
        description="Session-scoped message commands for a Bazaar Compute Node.",
    )
    subparsers = parser.add_subparsers(dest="resource", required=True)
    message_parser = subparsers.add_parser("message")
    message_subparsers = message_parser.add_subparsers(dest="command", required=True)
    message_subparsers.add_parser("check")

    read_parser = message_subparsers.add_parser("read")
    read_parser.add_argument("--target", required=True)
    read_parser.add_argument("--around")
    read_parser.add_argument("--limit", type=int, default=100)

    send_parser = message_subparsers.add_parser("send")
    send_parser.add_argument("--target", required=True)
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

    request: dict[str, object] = {
        "kind": "command",
        "session_id": session_id,
        "command": args.command,
    }
    if args.command == "read":
        request["target"] = args.target
        request["around_message_id"] = args.around
        request["limit"] = args.limit
    elif args.command == "send":
        request["target"] = args.target
        request["body"] = body if body is not None else ""
        request["command_id"] = f"bcc-{uuid7().hex}"

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


def _require_messages(result: Mapping[str, object]) -> list[Mapping[str, object]]:
    messages = result.get("messages")
    if not isinstance(messages, list):
        raise BccCommandError(
            "command response has no messages list",
            code="INVALID_RESPONSE",
        )
    if not all(isinstance(message, Mapping) for message in messages):
        raise BccCommandError(
            "command response contains an invalid message",
            code="INVALID_RESPONSE",
        )
    return messages


def _format_message_timestamp(message: Mapping[str, object]) -> str:
    timestamp = message.get("provider_time_ms") or message.get("received_at_ms")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise BccCommandError(
            "command response contains an invalid message timestamp",
            code="INVALID_RESPONSE",
        )
    return format_message_time(timestamp)


def _render_check(result: Mapping[str, object]) -> None:
    messages = _require_messages(result)
    for message in messages:
        print(
            "[target={target} msg={message_id} time={time} type={message_type}] "
            "@{sender}: {body}".format(
                target=message["canonical_target"],
                message_id=message["short_message_id"],
                time=_format_message_timestamp(message),
                message_type=message["message_type"],
                sender=message["sender_display_name"],
                body=message["body"],
            )
        )
    if not messages:
        print("No more new messages.")


def _render_read(result: Mapping[str, object]) -> None:
    messages = _require_messages(result)
    first_seq = result.get("first_seq")
    last_seq = result.get("last_seq")
    bounds = (
        f"{first_seq}-{last_seq}"
        if first_seq is not None and last_seq is not None
        else "none-none"
    )
    print(f"Read window: {len(messages)} returned, seq {bounds}, oldest to newest.")
    for index, message in enumerate(messages, start=1):
        fields = [
            f"seq={message['seq']}",
            f"msg={message['message_id']}",
            f"time={_format_message_timestamp(message)}",
            f"type={message['message_type']}",
        ]
        if message.get("provider_thread_id") is not None:
            fields.append(f"threadId={message['provider_thread_id']}")
        if message.get("reply_to_provider_message_id") is not None:
            fields.append(f"replyTarget={message['reply_to_provider_message_id']}")
        print(
            f"[{index}/{len(messages)} {' '.join(fields)}] "
            f"@{message['sender_display_name']}: {message['body']}"
        )


def _render_send(result: Mapping[str, object]) -> None:
    outbound = result.get("outbound")
    if not isinstance(outbound, Mapping):
        raise BccCommandError(
            "command response has no outbound object",
            code="INVALID_RESPONSE",
        )
    state = outbound.get("state")
    if state == "sent":
        print(
            f"Message sent to {outbound['target']}. "
            f"Message ID: {outbound['outbound_message_id']}"
        )
        return
    error_kind = outbound.get("error_kind")
    code = {
        "fresh_check_required": "SEND_FRESH_CHECK_REQUIRED",
        "fresh_check_failed": "SEND_FRESH_CHECK_FAILED",
        "provider_failed": "SEND_FAILED",
        "provider_unknown": "SEND_UNKNOWN",
    }.get(str(error_kind), "SEND_REJECTED")
    raise BccCommandError(
        str(outbound.get("error_message") or "outbound delivery was not sent"),
        code=code,
        draft_saved=outbound.get("draft_saved_at_ms") is not None,
        next_action=(
            str(outbound["next_action"])
            if outbound.get("next_action") is not None
            else None
        ),
    )


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    body = None
    if args.command == "send":
        body = await asyncio.to_thread(sys.stdin.read)
    response = await _request(args, body=body)
    result = _require_result(response)
    if args.command == "check":
        _render_check(result)
    elif args.command == "read":
        _render_read(result)
    else:
        _render_send(result)
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
