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
        "session_id": session_id,
        "runtime_session_id": runtime_session_id,
        "session_capability": session_capability,
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


def _invalid_response(message: str) -> NoReturn:
    raise BccCommandError(message, code="INVALID_RESPONSE")


def _require_text(
    message: Mapping[str, object],
    field_name: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = message.get(field_name)
    if not isinstance(value, str) or (not allow_empty and not value):
        _invalid_response(
            f"command response contains an invalid message {field_name}"
        )
    return value


def _require_non_negative_int(
    message: Mapping[str, object],
    field_name: str,
) -> int:
    value = message.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid_response(
            f"command response contains an invalid message {field_name}"
        )
    return value


def _require_result_sequence(result: Mapping[str, object], field_name: str) -> int:
    value = result.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid_response(f"command response contains an invalid {field_name}")
    return value


def _message_timestamp(message: Mapping[str, object]) -> int:
    timestamp = message.get("provider_time_ms")
    if timestamp is None:
        timestamp = message.get("received_at_ms")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        _invalid_response("command response contains an invalid message timestamp")
    return timestamp


def _format_message_timestamp(message: Mapping[str, object]) -> str:
    return format_message_time(_message_timestamp(message))


def _message_header_fields(message: Mapping[str, object]) -> tuple[str, ...]:
    target = _require_text(message, "canonical_target")
    message_id = _require_text(message, "message_id")
    short_message_id = _require_text(message, "short_message_id")
    if short_message_id != message_id[:8]:
        _invalid_response("command response contains an inconsistent message id")
    return (
        target,
        message_id,
        short_message_id,
        _format_message_timestamp(message),
        _require_text(message, "message_type"),
        _require_text(message, "sender_display_name"),
        _require_text(message, "body", allow_empty=True),
    )


def _format_check_message(message: Mapping[str, object]) -> str:
    (
        target,
        _message_id,
        short_message_id,
        timestamp,
        message_type,
        sender,
        body,
    ) = _message_header_fields(message)
    return (
        f"[target={target} msg={short_message_id} time={timestamp} "
        f"type={message_type}] @{sender}: {body}"
    )


def _format_read_message(
    message: Mapping[str, object],
    *,
    index: int,
    count: int,
) -> str:
    (
        target,
        message_id,
        _short_message_id,
        timestamp,
        message_type,
        sender,
        body,
    ) = _message_header_fields(message)
    fields = [
        f"seq={_require_non_negative_int(message, 'seq')}",
        f"msg={message_id}",
        f"time={timestamp}",
        f"type={message_type}",
    ]
    provider_thread_id = message.get("provider_thread_id")
    if provider_thread_id is not None:
        if not isinstance(provider_thread_id, str) or not provider_thread_id:
            _invalid_response(
                "command response contains an invalid message provider_thread_id"
            )
        fields.append(f"threadId={provider_thread_id}")
    fields.append(f"replyTarget={target}")
    return f"[{index}/{count} {' '.join(fields)}] @{sender}: {body}"


def serialize_check(result: Mapping[str, object]) -> str:
    """Serialize one check result using the stable agent-facing text contract."""

    snapshot_seq = _require_result_sequence(result, "snapshot_seq")
    delivered_through_seq = _require_result_sequence(result, "delivered_through_seq")
    if delivered_through_seq > snapshot_seq:
        _invalid_response(
            "command response contains an invalid check sequence boundary"
        )
    messages = _require_messages(result)
    lines = [_format_check_message(message) for message in messages]
    if not lines:
        lines.append("No more new messages.")
    return "\n".join(lines)


def serialize_read(result: Mapping[str, object]) -> str:
    """Serialize one read result with history positioning fields."""

    _require_result_sequence(result, "snapshot_seq")
    messages = _require_messages(result)
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
        first_message_seq = _require_non_negative_int(messages[0], "seq")
        last_message_seq = _require_non_negative_int(messages[-1], "seq")
        if first_seq != first_message_seq or last_seq != last_message_seq:
            _invalid_response("command response read bounds do not match messages")
        bounds = f"{first_seq}-{last_seq}"
    lines = [f"Read window: {len(messages)} returned, seq {bounds}, oldest to newest."]
    lines.extend(
        _format_read_message(message, index=index, count=len(messages))
        for index, message in enumerate(messages, start=1)
    )
    return "\n".join(lines)


def _render_check(result: Mapping[str, object]) -> None:
    print(serialize_check(result))


def _render_read(result: Mapping[str, object]) -> None:
    print(serialize_read(result))


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
