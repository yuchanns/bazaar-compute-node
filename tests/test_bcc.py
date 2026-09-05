from __future__ import annotations

from datetime import UTC, datetime

import click
import pytest

from bazaar_compute_node.cmd.bcc import bcc
from bazaar_compute_node.cmd.bcc._client import BccCommandError
from bazaar_compute_node.cmd.bcc._format import (
    print_error,
    serialize_check,
    serialize_read,
    serialize_send,
    serialize_unfollow,
)


def subcommand(group: click.Command, *path: str) -> click.Command:
    resolved = group
    for name in path:
        assert isinstance(resolved, click.Group)
        resolved = resolved.commands[name]
    return resolved


def message_payload(
    *,
    message_id: str = "0123456789abcdef0123456789abcdef",
    seq: int = 7,
    provider_time_ms: int | None = 1_700_000_000_000,
    received_at_ms: int = 1_700_000_000_001,
    target: str = "#work:parent123",
    provider_thread_id: str | None = "provider-thread-1",
    sender_id: str | None = "sender-id",
    sender_name: str | None = "sender",
    sender_display_name: str | None = None,
    sender_kind: str = "human",
    message_type: str = "text",
    reply_to_message_id: str | None = None,
) -> dict[str, object]:
    return {
        "seq": seq,
        "message_id": message_id,
        "target": target,
        "canonical_target": target,
        "provider_time_ms": provider_time_ms,
        "received_at_ms": received_at_ms,
        "message_type": message_type,
        "sender_kind": sender_kind,
        "sender": (
            None
            if sender_id is None and sender_name is None
            else {
                "id": sender_id,
                "name": sender_name,
                "display_name": sender_display_name,
            }
        ),
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


def test_check_serializer_matches_text() -> None:
    result = {
        "messages": [message_payload()],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    assert serialize_check(result) == (
        "[target=#work:parent123 msg=0123456789abcdef0123456789abcdef "
        f"time={local_time(1_700_000_000_000)} "
        "type=human] @sender: message body"
    )


def test_check_serializer_renders_sender_kind() -> None:
    # an agent sender kind replaces the content type
    result = {
        "messages": [message_payload(sender_kind="agent", message_type="text")],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    output = serialize_check(result)
    assert "type=agent" in output
    assert "type=text" not in output

    # an unrecognised sender kind is passed through
    result = {
        "messages": [message_payload(sender_kind="unknown")],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    assert "type=unknown" in serialize_check(result)


def test_only_message_check_appends_system_message_suffixes() -> None:
    message = message_payload(
        sender_id=None,
        sender_name="system",
        sender_kind="system",
    )
    reminder_cases = (
        (
            '🔔 Reminder #019c1234 (one-time) — dm:alice — "Review"',
            "(to snooze/cancel: bcc reminder --help)",
        ),
        (
            (
                "🔔 Reminder #019c1234 (recurring · every:15m) — dm:alice — "
                '"Review"\nNext iteration: 2026-08-25T04:15:00.000Z'
            ),
            "(to snooze/update/cancel: bcc reminder --help)",
        ),
    )
    for body, operation in reminder_cases:
        message["body"] = body
        message["system_message_kind"] = "reminder"
        checked = serialize_check(
            {
                "messages": [message],
                "referenced_messages": [],
                "snapshot_seq": 7,
                "delivered_through_seq": 7,
            }
        )
        read = serialize_read(
            {
                "messages": [message],
                "referenced_messages": [],
                "snapshot_seq": 7,
                "first_seq": 7,
                "last_seq": 7,
            }
        )
        assert checked.endswith(
            f"{body}\n{operation}\n"
            "Respond as appropriate. Complete all your work before stopping.\n"
            "Reply in the channel or create/reply in a thread as appropriate; "
            "use each message's `target` and `msg` fields to choose the exact target."
        )
        assert operation not in read
        assert "Respond as appropriate" not in read
        assert body in read

    handoff_body = (
        "🤝 Handoff from group:source — message outbound-message-1 was sent "
        "here from that conversation."
    )
    message.update(
        body=handoff_body,
        system_message_kind="handoff",
        system_message_source_target="group:source",
        system_message_source_message_id="source-message-1",
    )
    handoff_result = {
        "messages": [message],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }
    checked = serialize_check(handoff_result)
    read = serialize_read(
        {
            "messages": [message],
            "referenced_messages": [],
            "snapshot_seq": 7,
            "first_seq": 7,
            "last_seq": 7,
        },
    )
    assert checked.endswith(
        f"{handoff_body}\n"
        "To understand why this message was sent, inspect the source context:\n"
        '  bcc message read --target "group:source" --around "source-message-1"\n'
        "If you have no objection to why the message was sent, do not announce "
        "or explain the handoff, and do not repeat or respond to the referenced "
        "message; it has already been delivered. Continue only work already in "
        "progress in this conversation that is independent of that message; if "
        "there is none, stop.\n"
        "Mention the handoff only when its reason is unclear, conflicts with the "
        "current conversation, or requires a decision."
    )
    assert "inspect the source context" not in read
    assert handoff_body in read


def test_check_serializer_renders_sender_and_time_fallbacks() -> None:
    # the handle is what an Agent addresses the sender by
    result = {
        "messages": [
            message_payload(sender_name="test-user", sender_display_name="Test User")
        ],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    assert "@test-user(Test User): message body" in serialize_check(result)

    # a sender the provider gives no human name for is still addressable
    result = {
        "messages": [message_payload(sender_name="test-user")],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    assert "@test-user: message body" in serialize_check(result)

    # the sender id is only a fallback for a missing handle
    result = {
        "messages": [
            message_payload(sender_name=None, sender_display_name="Test User")
        ],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    assert "@sender-id(Test User): message body" in serialize_check(result)

    result = {
        "messages": [message_payload(sender_name=None)],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    assert "@sender-id: message body" in serialize_check(result)

    # a zero provider timestamp is kept, not treated as missing
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
        sender_id=None,
        sender_name=None,
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
    target = next(
        parameter
        for parameter in subcommand(bcc, "thread", "unfollow").params
        if parameter.name == "target"
    )

    assert target.required is True


def test_message_send_accepts_repeated_attachments_and_draft_mode() -> None:
    parameters = {
        parameter.name: parameter
        for parameter in subcommand(bcc, "message", "send").params
    }

    attachment = parameters["attachment"]
    send_draft = parameters["send_draft"]
    assert isinstance(attachment, click.Option)
    assert isinstance(send_draft, click.Option)
    assert attachment.multiple is True
    assert send_draft.is_flag is True


def test_read_serializer() -> None:
    # positioning and reply target are rendered
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
        "type=human replyTarget=#work:parent123] @sender: message body"
    )

    # absent thread metadata renders as empty
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


def test_send_serializer_prints_direct_text_verbatim() -> None:
    text = "Unreviewed synced context.\nChoose the next action."

    assert serialize_send({"text": text}) == text

    with pytest.raises(BccCommandError, match="invalid response") as error:
        serialize_send({"outbound": {}})
    assert error.value.code == "SEND_RESPONSE_INVALID"


def test_read_accepts_history_positioning_arguments() -> None:
    parameters = {
        parameter.name: parameter
        for parameter in subcommand(bcc, "message", "read").params
    }

    assert parameters["target"].required is True
    assert parameters["around"].required is False
    assert parameters["limit"].default == 100


def test_error_contract_is_stderr_only(capsys: pytest.CaptureFixture[str]) -> None:
    print_error(
        BccCommandError(
            "history target is invalid",
            code="INVALID_TARGET",
            next_action="Run `bcc message read` with a valid target.",
        )
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Error: history target is invalid\n"
        "Code: INVALID_TARGET\n"
        "Next action: Run `bcc message read` with a valid target.\n"
    )


def test_error_contract_reports_a_saved_draft(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_error(
        BccCommandError(
            "the channel rejected the message",
            code="SEND_FAILED",
            draft_saved=True,
            next_action="Revise the draft and send it again.",
        )
    )

    captured = capsys.readouterr()
    lines = captured.err.splitlines()

    # case: a failed send tells the agent its text was not lost, and what to do
    assert any(line.startswith("Draft saved:") for line in lines)
    assert any("Revise the draft" in line for line in lines)
    assert captured.out == ""


def test_unfollow_distinguishes_a_change_from_a_repeat() -> None:
    target = "#work:parent123"

    # case: the agent learns whether its command actually changed anything
    assert (
        serialize_unfollow({"target": target, "changed": True})
        == f"Thread unfollowed: {target}"
    )
    assert (
        serialize_unfollow({"target": target, "changed": False})
        == f"Thread was already unfollowed: {target}"
    )


def test_check_keeps_referenced_context_when_nothing_is_new() -> None:
    referenced = message_payload()

    output = serialize_check({"messages": [], "referenced_messages": [referenced]})

    # case: the referenced block still announces where new messages would go,
    # and the output does not trail off with a blank line
    assert output.endswith("New messages:")
    assert "Referenced messages: 1" in output
