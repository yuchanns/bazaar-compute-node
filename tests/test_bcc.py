from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bazaar_compute_node.bcc import (
    BccCommandError,
    _print_error,
    build_parser,
    serialize_check,
    serialize_inbox_list,
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
    sender_id: str | None = "sender-id",
    sender_name: str | None = "sender",
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
            else {"id": sender_id, "name": sender_name}
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


def inbox_target_payload(
    *,
    target: str = "dm:alice",
    session_id: str = "session-a",
    target_kind: str = "dm",
    current: bool = False,
    pending_count: int = 0,
    last_activity_at_ms: int = 1_700_000_000_002,
    latest_message_id: str | None = "latest-message",
    latest_sender: dict[str, str] | None = None,
    latest_time_ms: int | None = 1_700_000_000_000,
) -> dict[str, object]:
    return {
        "target": target,
        "session_id": session_id,
        "target_kind": target_kind,
        "current": current,
        "pending_count": pending_count,
        "last_activity_at_ms": last_activity_at_ms,
        "latest_message_id": latest_message_id,
        "latest_sender": (
            {"id": "alice-id", "name": "alice"}
            if latest_sender is None and latest_message_id is not None
            else latest_sender
        ),
        "latest_time_ms": latest_time_ms,
    }


def test_inbox_list_serializer() -> None:
    # a page with more targets to come
    result = {
        "targets": [
            inbox_target_payload(
                target="dm:alice",
                session_id="session-a",
                current=False,
                pending_count=0,
            ),
            inbox_target_payload(
                target="group:team",
                session_id="session-b",
                target_kind="group",
                current=True,
                pending_count=3,
                last_activity_at_ms=1_700_000_000_001,
                latest_message_id="team-message",
                latest_sender={"id": "bob-id", "name": "bob"},
                latest_time_ms=1_700_000_000_001,
            ),
        ],
        "total": 3,
        "shown": 2,
        "offset": 0,
        "has_more": True,
    }

    assert serialize_inbox_list(result) == (
        "Inbox targets: 2 returned, offset 0, total 3, "
        "ordered by recent activity.\n"
        "[target=dm:alice session=session-a kind=dm current=false pending=0 "
        "latest-msg=latest-message latest-sender=@alice "
        f"latest-time={local_time(1_700_000_000_000)}]\n"
        "[target=group:team session=session-b kind=group current=true pending=3 "
        "latest-msg=team-message latest-sender=@bob "
        f"latest-time={local_time(1_700_000_000_001)}]\n"
        "More message targets remain. Run `bcc inbox list --offset 2`."
    )

    # a target that has no latest message
    result = {
        "targets": [
            inbox_target_payload(
                target="dm:empty",
                session_id="session-empty",
                last_activity_at_ms=0,
                latest_message_id=None,
                latest_sender=None,
                latest_time_ms=None,
            )
        ],
        "total": 1,
        "shown": 1,
        "offset": 0,
        "has_more": False,
    }

    assert serialize_inbox_list(result) == (
        "Inbox targets: 1 returned, offset 0, total 1, "
        "ordered by recent activity.\n"
        "[target=dm:empty session=session-empty kind=dm current=false pending=0 "
        "latest-msg=none latest-sender=none latest-time=none]\n"
        "No more message targets."
    )

    # an empty catalog
    assert serialize_inbox_list(
        {
            "targets": [],
            "total": 0,
            "shown": 0,
            "offset": 0,
            "has_more": False,
        }
    ) == (
        "Inbox targets: 0 returned, offset 0, total 0, "
        "ordered by recent activity.\n"
        "No message targets."
    )

    # an empty page past the end of a catalog
    assert serialize_inbox_list(
        {
            "targets": [],
            "total": 1,
            "shown": 0,
            "offset": 1,
            "has_more": False,
        }
    ) == (
        "Inbox targets: 0 returned, offset 1, total 1, "
        "ordered by recent activity.\n"
        "No more message targets."
    )


def test_inbox_list_parser_accepts_pagination_arguments() -> None:
    args = build_parser().parse_args(("inbox", "list", "--limit", "3", "--offset", "6"))

    assert args.resource == "inbox"
    assert args.command == "list"
    assert args.limit == 3
    assert args.offset == 6


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
        "type=human] @sender-id(sender): message body"
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
    # a provider username is preferred for the sender
    result = {
        "messages": [message_payload(sender_name="test-user")],
        "referenced_messages": [],
        "snapshot_seq": 7,
        "delivered_through_seq": 7,
    }

    assert "@sender-id(test-user): message body" in serialize_check(result)

    # the sender id is used when no username is known
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
    args = build_parser().parse_args(
        ("thread", "unfollow", "--target", "#work:parent123")
    )

    assert args.resource == "thread"
    assert args.command == "unfollow"
    assert args.target == "#work:parent123"


def test_message_send_parser_accepts_attachments_and_draft_mode() -> None:
    # attachments keep their order and repeat
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
    assert args.send_draft is False

    # draft mode is accepted without a body
    parser = build_parser()
    args = parser.parse_args(
        ("message", "send", "--send-draft", "--target", "#work:parent123")
    )

    assert args.send_draft is True
    assert args.reply_to is None
    assert args.attachment == []
    assert not {
        "source",
        "source_target",
        "source_target_id",
        "target_id",
    }.intersection(vars(args))
    with pytest.raises(SystemExit):
        parser.parse_args(
            (
                "message",
                "send",
                "--target",
                "#work:parent123",
                "--source-target",
                "#work:source",
            )
        )


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
        "type=human replyTarget=#work:parent123] @sender-id(sender): message body"
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
