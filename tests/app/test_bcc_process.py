from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import cast

import pytest
from bcn_test_support import RecordingAudit, TestChannel, TestRuntime

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.registry import AdapterFactories
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.contrib.sqlite.repository import SqliteTransaction
from bazaar_compute_node.core.channel import ChannelDeliveryReceipt
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import InboundMessage, RuntimeSession
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.runtime import RuntimeCommandContext


def make_budget() -> TimeoutBudget:
    return TimeoutBudget(
        startup_seconds=2,
        provider_call_seconds=2,
        command_seconds=2,
        shutdown_seconds=2,
    )


def make_factories() -> AdapterFactories:
    def create_runtime(_context: RuntimeCommandContext) -> TestRuntime:
        return TestRuntime()

    def create_storage() -> SqliteDatabase:
        return SqliteDatabase()

    return AdapterFactories(
        channel=lambda _context: TestChannel(),
        runtime=create_runtime,
        storage=create_storage,
        audit=RecordingAudit,
    )


def make_message(
    seq: int,
    *,
    body: str,
    sender: str | None = "sender",
    notifies_runtime: bool = True,
    reply_to_message_id: str | None = None,
) -> InboundMessage:
    return InboundMessage(
        seq=seq,
        message_id=f"input-message-{seq}",
        session_id="bcn-a",
        channel_session_id="channel-bcn-a",
        channel="test",
        provider_thread_id="provider-thread-a",
        provider_message_id=f"provider-message-{seq}",
        received_at_ms=1_000 + seq,
        provider_time_ms=2_000 + seq,
        sender=sender,
        message_type="human",
        canonical_target="#test:bcn-a",
        body=body,
        notifies_runtime=notifies_runtime,
        reply_to_message_id=reply_to_message_id,
    )


async def wait_for_messages(
    node: NodeApplication, count: int
) -> tuple[InboundMessage, ...]:
    for _ in range(200):
        async with node.storage.transaction() as transaction:
            messages = await transaction.list_inbound_messages("bcn-a")
        if len(messages) >= count:
            return messages
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} persisted inbound messages")


async def wait_for_runtime_session(node: NodeApplication) -> RuntimeSession:
    for _ in range(200):
        async with node.storage.transaction() as transaction:
            runtime_session = await transaction.find_runtime_session("bcn-a")
        if runtime_session is not None:
            return runtime_session
        await asyncio.sleep(0.01)
    raise AssertionError("runtime session was not persisted")


async def run_bcc(
    node: NodeApplication,
    runtime_session: RuntimeSession,
    arguments: tuple[str, ...],
    *,
    body: str | None = None,
) -> tuple[int, str, str]:
    wrapper_path = node._wrapper_path
    if wrapper_path is None:
        raise AssertionError("bcc wrapper was not installed")
    environment = dict(
        cast(
            dict[str, str],
            node._runtime_environment(runtime_session),
        )
    )
    process = await asyncio.create_subprocess_exec(
        str(wrapper_path),
        *arguments,
        stdin=asyncio.subprocess.PIPE
        if body is not None
        else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    stdout, stderr = await process.communicate(
        body.encode() if body is not None else None
    )
    return (
        process.returncode if process.returncode is not None else -1,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


@pytest.mark.asyncio
async def test_real_sqlite_bcc_check_read_and_snapshot_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BCN_WECOM_BOT_SECRET", "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("CODEX_HOME", "must-not-reach-test-runtime")
    monkeypatch.setenv("TEST_RUNTIME_HOME", "test-runtime-home")
    factories = make_factories()
    node = NodeApplication(
        factories=factories,
        endpoint_path=tmp_path / "bcn.sock",
        node_id="node-3c",
        timeout_budget=make_budget(),
    )
    channel = cast(TestChannel, node.channel)
    await node.start()
    try:
        await channel.inject(
            make_message(
                1,
                body="quoted inbound",
                sender=None,
                notifies_runtime=False,
            )
        )
        await channel.inject(
            make_message(
                2,
                body="current inbound",
                reply_to_message_id="input-message-1",
            )
        )
        messages = await wait_for_messages(node, 2)
        runtime_session = await wait_for_runtime_session(node)
        runtime_environment = node._runtime_environment(runtime_session)
        assert "BCN_WECOM_BOT_SECRET" not in runtime_environment
        assert "OPENAI_API_KEY" not in runtime_environment
        assert "CODEX_HOME" not in runtime_environment
        assert runtime_environment["TEST_RUNTIME_HOME"] == "test-runtime-home"
        assert {
            "BCN_ENDPOINT",
            "BCN_SESSION_ID",
            "BCN_RUNTIME_SESSION_ID",
            "BCN_COMMAND_CAPABILITY",
        } <= runtime_environment.keys()

        read_code, read_stdout, read_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "read", "--target", "#test:bcn-a", "--limit", "2"),
        )
        assert read_code == 0, read_stderr
        assert read_stderr == ""
        assert read_stdout.splitlines()[0] == (
            "Read window: 2 returned, seq 1-2, oldest to newest."
        )
        assert "provider-thread-a" not in read_stdout
        assert "provider-message" not in read_stdout
        assert "replyTarget=#test:bcn-a" in read_stdout
        assert messages[0].message_id in read_stdout
        assert messages[1].message_id in read_stdout
        assert read_stdout.index("quoted inbound") < read_stdout.index(
            "current inbound"
        )

        async with node.storage.transaction() as transaction:
            cursor = await transaction.get_consumer_cursor("bcn-a")
        assert cursor is not None
        assert cursor.delivered_through_seq == 0
        assert cursor.inbox_snapshot_seq == 2

        around_code, around_stdout, around_stderr = await run_bcc(
            node,
            runtime_session,
            (
                "message",
                "read",
                "--target",
                "#test:bcn-a",
                "--around",
                messages[1].message_id,
                "--limit",
                "1",
            ),
        )
        assert around_code == 0, around_stderr
        assert "Read window: 1 returned, seq 2-2" in around_stdout
        assert "Referenced messages: 1" in around_stdout
        assert "quoted inbound" in around_stdout
        assert "current inbound" in around_stdout

        check_code, check_stdout, check_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "check"),
        )
        assert check_code == 0, check_stderr
        assert check_stderr == ""
        assert "Referenced messages: 1" in check_stdout
        assert "reply_to=input-message-1" in check_stdout
        assert "[target=#test:bcn-a msg=input-message-2 " in check_stdout
        assert "quoted inbound" in check_stdout
        assert "current inbound" in check_stdout
        assert "No more new messages." not in check_stdout

        async with node.storage.transaction() as transaction:
            cursor = await transaction.get_consumer_cursor("bcn-a")
        assert cursor is not None
        assert cursor.delivered_through_seq == 2
        assert cursor.inbox_snapshot_seq == 2

        empty_code, empty_stdout, empty_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "check"),
        )
        assert empty_code == 0
        assert empty_stdout.splitlines() == ["No more new messages."]
        assert empty_stderr == ""

        bad_around_code, bad_around_stdout, bad_around_stderr = await run_bcc(
            node,
            runtime_session,
            (
                "message",
                "read",
                "--target",
                "#test:bcn-a",
                "--around",
                "missing-message-id",
            ),
        )
        assert bad_around_code != 0
        assert bad_around_stdout == ""
        assert "Error: message not found in requested history" in bad_around_stderr
        assert "Code: INVALID_COMMAND" in bad_around_stderr
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_real_sqlite_bcc_send_safety_gate_and_delivery_states(
    tmp_path: Path,
) -> None:
    node = NodeApplication(
        factories=make_factories(),
        endpoint_path=tmp_path / "bcn.sock",
        node_id="node-3d",
        timeout_budget=make_budget(),
    )
    channel = cast(TestChannel, node.channel)
    audit = cast(RecordingAudit, node.audit)
    await node.start()
    try:
        attachment_path = node._workspace_path() / "report.txt"
        attachment_path.write_text("report\n")
        await channel.inject(make_message(1, body="first inbound"))
        await wait_for_messages(node, 1)
        runtime_session = await wait_for_runtime_session(node)

        (
            missing_snapshot_code,
            missing_snapshot_stdout,
            missing_snapshot_stderr,
        ) = await run_bcc(
            node,
            runtime_session,
            (
                "message",
                "send",
                "--target",
                "#test:bcn-a",
                "--attachment",
                str(attachment_path),
            ),
            body="",
        )
        assert missing_snapshot_code != 0
        assert missing_snapshot_stdout == ""
        assert "Error: No inbox snapshot is available" in missing_snapshot_stderr
        assert "Code: SEND_FRESH_CHECK_REQUIRED" in missing_snapshot_stderr
        assert "Draft saved: yes" in missing_snapshot_stderr
        assert len(channel.send_attempts) == 0
        async with node.storage.transaction() as transaction:
            sqlite_transaction = cast(SqliteTransaction, transaction)
            row = await sqlite_transaction.fetchone(
                "SELECT outbound_message_id FROM outbound_messages "
                "ORDER BY created_at_ms DESC LIMIT 1"
            )
            assert row is not None
            draft = await sqlite_transaction.get_outbound_message(
                row["outbound_message_id"]
            )
        assert draft is not None
        assert draft.body == ""
        assert len(draft.attachments) == 1
        assert draft.attachments[0].relative_path == "report.txt"

        read_code, _read_stdout, read_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "read", "--target", "#test:bcn-a"),
        )
        assert read_code == 0, read_stderr

        sent_code, sent_stdout, sent_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#test:bcn-a"),
            body="confirmed reply",
        )
        assert sent_code == 0, sent_stderr
        assert sent_stderr == ""
        assert sent_stdout.startswith("Message sent to #test:bcn-a. Message ID: ")
        assert len(channel.sent_messages) == 1

        attachment_code, attachment_stdout, attachment_stderr = await run_bcc(
            node,
            runtime_session,
            (
                "message",
                "send",
                "--target",
                "#test:bcn-a",
                "--attachment",
                str(attachment_path),
            ),
            body="",
        )
        assert attachment_code == 0, attachment_stderr
        assert attachment_stdout.startswith("Message sent to #test:bcn-a. Message ID: ")
        attachment_message = channel.sent_messages[-1]
        assert attachment_message.body == ""
        assert [attachment.name for attachment in attachment_message.attachments] == [
            "report.txt"
        ]
        assert attachment_message.attachments[0].relative_path == "report.txt"

        duplicate_code, duplicate_stdout, duplicate_stderr = await run_bcc(
            node,
            runtime_session,
            (
                "message",
                "send",
                "--target",
                "#test:bcn-a",
                "--attachment",
                str(attachment_path),
                "--attachment",
                str(attachment_path),
            ),
            body="duplicate",
        )
        assert duplicate_code != 0
        assert duplicate_stdout == ""
        assert "attachment paths must not contain duplicates" in duplicate_stderr

        outside_path = tmp_path / "outside.txt"
        outside_path.write_text("outside\n")
        outside_code, outside_stdout, outside_stderr = await run_bcc(
            node,
            runtime_session,
            (
                "message",
                "send",
                "--target",
                "#test:bcn-a",
                "--attachment",
                str(outside_path),
            ),
            body="outside",
        )
        assert outside_code != 0
        assert outside_stdout == ""
        assert "attachment path must stay within the workspace" in outside_stderr

        if os.name != "nt":
            symlink_path = node._workspace_path() / "report-link.txt"
            symlink_path.symlink_to(attachment_path)
            symlink_code, symlink_stdout, symlink_stderr = await run_bcc(
                node,
                runtime_session,
                (
                    "message",
                    "send",
                    "--target",
                    "#test:bcn-a",
                    "--attachment",
                    str(symlink_path),
                ),
                body="symlink",
            )
            assert symlink_code != 0
            assert symlink_stdout == ""
            assert "attachment path cannot contain symbolic links" in symlink_stderr

        (
            empty_body_code,
            empty_body_stdout,
            empty_body_stderr,
        ) = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#test:bcn-a"),
            body="",
        )
        assert empty_body_code != 0
        assert empty_body_stdout == ""
        assert "Error: Outbound message must not be empty." in empty_body_stderr
        assert "Code: SEND_EMPTY_BODY" in empty_body_stderr
        assert "Draft saved:" not in empty_body_stderr
        assert (
            "Next action: Provide a message body or attachment and retry."
            in empty_body_stderr
        )
        assert len(channel.send_attempts) == 2

        (
            invalid_target_code,
            invalid_target_stdout,
            invalid_target_stderr,
        ) = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#test:missing"),
            body="invalid target reply",
        )
        assert invalid_target_code != 0
        assert invalid_target_stdout == ""
        assert (
            "Error: Thread target is not found or is not replyable: #test:missing"
            in invalid_target_stderr
        )
        assert "Code: SEND_FAILED" in invalid_target_stderr
        assert "Draft saved: yes" in invalid_target_stderr
        assert len(channel.send_attempts) == 2

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.QUEUED,
                value=ChannelDeliveryReceipt(provider_receipt_ref="queue-1"),
            )
        )
        queued_code, queued_stdout, queued_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#test:bcn-a"),
            body="queued reply",
        )
        assert queued_code == 0, queued_stderr
        assert queued_stderr == ""
        assert queued_stdout.startswith("Message queued to #test:bcn-a. Message ID: ")
        assert len(channel.queued_messages) == 1

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.UNKNOWN,
                error_kind="transport_eof",
                error_message="delivery outcome is unknown",
            )
        )
        unknown_code, unknown_stdout, unknown_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#test:bcn-a"),
            body="unknown reply",
        )
        assert unknown_code != 0
        assert unknown_stdout == ""
        assert "Code: SEND_UNKNOWN" in unknown_stderr
        assert (
            "Next action: reconcile channel delivery before retrying" in unknown_stderr
        )

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_rejected",
                error_message="provider rejected delivery",
            )
        )
        failed_code, failed_stdout, failed_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#test:bcn-a"),
            body="failed reply",
        )
        assert failed_code != 0
        assert failed_stdout == ""
        assert "Error: provider rejected delivery" in failed_stderr
        assert "Code: SEND_FAILED" in failed_stderr

        await channel.inject(make_message(2, body="new inbound"))
        await wait_for_messages(node, 2)
        stale_code, stale_stdout, stale_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#test:bcn-a"),
            body="stale reply",
        )
        assert stale_code != 0
        assert stale_stdout == ""
        assert "Code: SEND_FRESH_CHECK_FAILED" in stale_stderr
        assert "Draft saved: yes" in stale_stderr
        assert len(channel.send_attempts) == 5
        assert any(
            event.event_name == "channel.outbound.pending" for event in audit.events
        )
        assert any(
            event.event_name == "bcc.send.fresh_check.passed" for event in audit.events
        )
        assert any(
            event.event_name == "channel.outbound.queued" for event in audit.events
        )
        assert any(
            event.event_name == "channel.outbound.unknown" for event in audit.events
        )
        assert any(
            event.event_name == "channel.outbound.failed" for event in audit.events
        )
    finally:
        await node.stop()
