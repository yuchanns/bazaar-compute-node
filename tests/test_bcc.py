from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bazaar_compute_node.bcc import (
    BccCommandError,
    _print_error,
    build_parser,
    serialize_check,
    serialize_read,
    serialize_send,
)


def message_payload(
    *,
    message_id: str = "0123456789abcdef0123456789abcdef",
    seq: int = 7,
    provider_time_ms: int | None = 1_700_000_000_000,
    received_at_ms: int = 1_700_000_000_001,
    target: str = "#work:parent123",
    provider_thread_id: str | None = "provider-thread-1",
    sender: str | None = "sender",
    reply_to_message_id: str | None = None,
) -> dict[str, object]:
    return {
        "seq": seq,
        "message_id": message_id,
        "canonical_target": target,
        "provider_time_ms": provider_time_ms,
        "received_at_ms": received_at_ms,
        "message_type": "human",
        "sender": sender,
        "body": "message body",
        "provider_thread_id": provider_thread_id,
        "reply_to_message_id": reply_to_message_id,
        "mentions_agent": False,
        "attachments": [],
    }


def local_time(timestamp_ms: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def test_check_serializer_matches_canonical_text() -> None:
    result = {
        "messages": [message_payload()],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    assert serialize_check(result) == (
        "[target=#work:parent123 msg=0123456789abcdef0123456789abcdef "
        f"time={local_time(1_700_000_000_000)} "
        "type=human mentioned=false] @sender: message body"
    )


def test_check_serializer_renders_provider_username_as_sender() -> None:
    result = {
        "messages": [message_payload(sender="realyuchanns")],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    assert "@realyuchanns: message body" in serialize_check(result)


def test_check_serializer_preserves_zero_provider_timestamp() -> None:
    result = {
        "messages": [
            message_payload(provider_time_ms=0, received_at_ms=1_700_000_000_000)
        ],
        "referenced_messages": [],
        "snapshot_seq": 1,
        "delivered_through_seq": 1,
    }

    assert f"time={local_time(0)}" in serialize_check(result)


def test_check_serializer_renders_referenced_message_before_current_message() -> None:
    referenced = message_payload(
        message_id="referenced-message-id",
        seq=6,
        sender=None,
    )
    referenced["body"] = "quoted body"
    current = message_payload(
        message_id="current-message-id",
        seq=7,
        reply_to_message_id="referenced-message-id",
    )
    current["body"] = "current body"
    result = {
        "messages": [current],
        "referenced_messages": [referenced],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    output = serialize_check(result)
    assert "Referenced messages: 1" in output
    assert "msg=referenced-message-id" in output
    assert "msg=current-message-id" in output
    assert "reply_to=referenced-message-id" in output
    assert output.index("quoted body") < output.index("current body")


def test_check_and_read_render_the_same_attachment_suffix() -> None:
    message = message_payload()
    message["attachments"] = [
        {
            "attachment_id": "attachment-1",
            "name": "report.txt",
            "kind": "file",
            "state": "ready",
            "media_type": "text/plain",
            "relative_path": "attachments/attachment-1/content.txt",
            "size_bytes": 7,
            "error": None,
        }
    ]
    suffix = (
        "[1 attachment: report.txt "
        "(id:attachment-1, path:attachments/attachment-1/content.txt)]"
    )

    assert suffix in serialize_check(
        {
            "messages": [message],
            "referenced_messages": [],
            "snapshot_seq": 7,
            "delivered_through_seq": 7,
        }
    )
    assert suffix in serialize_read(
        {
            "messages": [message],
            "referenced_messages": [],
            "snapshot_seq": 7,
            "first_seq": 7,
            "last_seq": 7,
        }
    )


def test_empty_check_serializer_is_stable() -> None:
    assert (
        serialize_check(
            {
                "messages": [],
                "referenced_messages": [],
                "snapshot_seq": 0,
                "delivered_through_seq": 0,
            }
        )
        == "No more new messages."
    )


def test_thread_unfollow_requires_an_explicit_target() -> None:
    args = build_parser().parse_args(
        ("thread", "unfollow", "--target", "#work:parent123")
    )

    assert args.resource == "thread"
    assert args.command == "unfollow"
    assert args.target == "#work:parent123"


def test_message_send_accepts_ordered_repeatable_attachments() -> None:
    args = build_parser().parse_args(
        (
            "message",
            "send",
            "--target",
            "#work:parent123",
            "--attachment",
            "first.txt",
            "--attachment",
            "second.png",
        )
    )

    assert args.attachment == ["first.txt", "second.png"]


def test_read_serializer_includes_positioning_and_canonical_reply_target() -> None:
    result = {
        "messages": [message_payload()],
        "referenced_messages": [],
        "snapshot_seq": 9,
        "first_seq": 7,
        "last_seq": 7,
    }

    assert serialize_read(result) == (
        "Read window: 1 returned, seq 7-7, oldest to newest.\n"
        "[1/1 seq=7 msg=0123456789abcdef0123456789abcdef "
        f"time={local_time(1_700_000_000_000)} "
        "type=human replyTarget=#work:parent123 "
        "mentioned=false] @sender: message body"
    )


def test_read_serializer_handles_empty_optional_thread_metadata() -> None:
    result = {
        "messages": [message_payload(provider_thread_id=None)],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "first_seq": 7,
        "last_seq": 7,
    }

    output = serialize_read(result)
    assert "threadId=" not in output
    assert "replyTarget=#work:parent123" in output


def test_read_serializer_rejects_mismatched_window_bounds() -> None:
    result = {
        "messages": [message_payload(seq=7)],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "first_seq": 6,
        "last_seq": 7,
    }

    with pytest.raises(BccCommandError) as error:
        serialize_read(result)

    assert error.value.code == "INVALID_RESPONSE"


def outbound_payload(
    *,
    state: str,
    error_kind: str | None = None,
    error_message: str | None = None,
    next_action: str | None = None,
    draft_saved_at_ms: int | None = None,
) -> dict[str, object]:
    return {
        "state": state,
        "target": "#work:parent123",
        "outbound_message_id": "0123456789abcdef0123456789abcdef",
        "error_kind": error_kind,
        "error_message": error_message,
        "next_action": next_action,
        "draft_saved_at_ms": draft_saved_at_ms,
    }


def test_send_serializer_matches_sent_and_queued_stdout_contracts() -> None:
    sent = {"outbound": outbound_payload(state="sent")}
    queued = {"outbound": outbound_payload(state="queued")}

    assert serialize_send(sent) == (
        "Message sent to #work:parent123. Message ID: 0123456789abcdef0123456789abcdef"
    )
    assert serialize_send(queued) == (
        "Message queued to #work:parent123. "
        "Message ID: 0123456789abcdef0123456789abcdef"
    )


def test_send_serializer_maps_refusal_to_stable_error_contract() -> None:
    result = {
        "outbound": outbound_payload(
            state="rejected",
            error_kind="fresh_check_failed",
            error_message="New inbound message(s) arrived after the latest inbox snapshot; outbound send was refused.",
            next_action="Run `bcc message check` before retrying.",
            draft_saved_at_ms=10,
        )
    }

    with pytest.raises(BccCommandError) as error:
        serialize_send(result)

    assert error.value.code == "SEND_FRESH_CHECK_FAILED"
    assert error.value.draft_saved is True
    assert error.value.next_action == "Run `bcc message check` before retrying."


def test_send_serializer_maps_empty_body_refusal() -> None:
    result = {
        "outbound": outbound_payload(
            state="rejected",
            error_kind="empty_body",
            error_message="Outbound message body must not be empty.",
            next_action="Provide a non-empty message body and retry.",
        )
    }

    with pytest.raises(BccCommandError) as error:
        serialize_send(result)

    assert error.value.code == "SEND_EMPTY_BODY"
    assert error.value.draft_saved is False
    assert error.value.next_action == "Provide a non-empty message body and retry."


def test_send_serializer_rejects_malformed_delivery_response() -> None:
    with pytest.raises(BccCommandError) as error:
        serialize_send(
            {
                "outbound": outbound_payload(
                    state="rejected",
                    error_kind="target_not_replyable",
                    error_message="target rejected",
                )
            }
        )

    assert error.value.code == "INVALID_RESPONSE"


def test_read_parser_accepts_history_positioning_arguments() -> None:
    args = build_parser().parse_args(
        [
            "message",
            "read",
            "--target",
            "#work:parent123",
            "--around",
            "0123456789abcdef0123456789abcdef",
            "--limit",
            "3",
        ]
    )

    assert args.command == "read"
    assert args.target == "#work:parent123"
    assert args.around == "0123456789abcdef0123456789abcdef"
    assert args.limit == 3


def test_error_contract_is_stderr_only(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_error:
        _print_error(
            BccCommandError(
                "history target is invalid",
                code="INVALID_TARGET",
                next_action="Run `bcc message read` with a valid target.",
            )
        )

    captured = capsys.readouterr()
    assert exit_error.value.code == 1
    assert captured.out == ""
    assert captured.err == (
        "Error: history target is invalid\n"
        "Code: INVALID_TARGET\n"
        "Next action: Run `bcc message read` with a valid target.\n"
    )
