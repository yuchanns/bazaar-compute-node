from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.registry import AdapterFactories
from bazaar_compute_node.contrib.dummy import DummyAudit, DummyChannel, DummyRuntime
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
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
    def create_runtime(_context: RuntimeCommandContext) -> DummyRuntime:
        return DummyRuntime()

    def create_storage(data_dir: Path) -> SqliteDatabase:
        return SqliteDatabase(data_dir)

    return AdapterFactories(
        channel=DummyChannel,
        runtime=create_runtime,
        storage=create_storage,
        audit=DummyAudit,
    )


def make_message(seq: int, *, body: str) -> InboundMessage:
    return InboundMessage(
        seq=seq,
        message_id=f"input-message-{seq}",
        bcn_session_id="bcn-a",
        channel_session_id="channel-bcn-a",
        channel_slug="dummy",
        provider_message_id=f"provider-message-{seq}",
        received_at_ms=1_000 + seq,
        provider_time_ms=2_000 + seq,
        sender_id="sender-id",
        sender_display_name="sender",
        message_type="human",
        canonical_target="#dummy:bcn-a",
        body=body,
        provider_thread_id="provider-thread-a",
        reply_to_provider_message_id="provider-parent-a",
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
) -> None:
    factories = make_factories()
    node = NodeApplication(
        factories=factories,
        channel_slug="dummy",
        runtime_slug="dummy",
        storage_slug="sqlite",
        audit_slug="dummy",
        data_dir=tmp_path / "data",
        endpoint_path=tmp_path / "bcn.sock",
        node_id="node-3c",
        timeout_budget=make_budget(),
    )
    channel = cast(DummyChannel, node.channel)
    await node.start()
    try:
        await channel.inject(make_message(1, body="first inbound"))
        await channel.inject(make_message(2, body="second inbound"))
        messages = await wait_for_messages(node, 2)
        runtime_session = await wait_for_runtime_session(node)

        read_code, read_stdout, read_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "read", "--target", "#dummy:bcn-a", "--limit", "2"),
        )
        assert read_code == 0, read_stderr
        assert read_stderr == ""
        assert read_stdout.startswith(
            "Read window: 2 returned, seq 1-2, oldest to newest.\n"
        )
        assert "threadId=provider-thread-a" in read_stdout
        assert "replyTarget=#dummy:bcn-a" in read_stdout
        assert messages[0].message_id in read_stdout
        assert messages[1].message_id in read_stdout
        assert read_stdout.index("first inbound") < read_stdout.index("second inbound")

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
                "#dummy:bcn-a",
                "--around",
                messages[1].message_id,
                "--limit",
                "1",
            ),
        )
        assert around_code == 0, around_stderr
        assert "Read window: 1 returned, seq 2-2" in around_stdout
        assert "second inbound" in around_stdout
        assert "first inbound" not in around_stdout

        check_code, check_stdout, check_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "check"),
        )
        assert check_code == 0, check_stderr
        assert check_stderr == ""
        assert "[target=#dummy:bcn-a msg=" in check_stdout
        assert "first inbound" in check_stdout
        assert "second inbound" in check_stdout
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
        assert empty_stdout == "No more new messages.\n"
        assert empty_stderr == ""

        bad_around_code, bad_around_stdout, bad_around_stderr = await run_bcc(
            node,
            runtime_session,
            (
                "message",
                "read",
                "--target",
                "#dummy:bcn-a",
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
        channel_slug="dummy",
        runtime_slug="dummy",
        storage_slug="sqlite",
        audit_slug="dummy",
        data_dir=tmp_path / "data",
        endpoint_path=tmp_path / "bcn.sock",
        node_id="node-3d",
        timeout_budget=make_budget(),
    )
    channel = cast(DummyChannel, node.channel)
    audit = cast(DummyAudit, node.audit)
    await node.start()
    try:
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
            ("message", "send", "--target", "#dummy:bcn-a"),
            body="reply before check",
        )
        assert missing_snapshot_code != 0
        assert missing_snapshot_stdout == ""
        assert "Error: No inbox snapshot is available" in missing_snapshot_stderr
        assert "Code: SEND_FRESH_CHECK_REQUIRED" in missing_snapshot_stderr
        assert "Draft saved: yes" in missing_snapshot_stderr
        assert len(channel.send_attempts) == 0

        read_code, _read_stdout, read_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "read", "--target", "#dummy:bcn-a"),
        )
        assert read_code == 0, read_stderr

        sent_code, sent_stdout, sent_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#dummy:bcn-a"),
            body="confirmed reply",
        )
        assert sent_code == 0, sent_stderr
        assert sent_stderr == ""
        assert sent_stdout.startswith("Message sent to #dummy:bcn-a. Message ID: ")
        assert len(channel.sent_messages) == 1

        (
            invalid_target_code,
            invalid_target_stdout,
            invalid_target_stderr,
        ) = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#dummy:missing"),
            body="invalid target reply",
        )
        assert invalid_target_code != 0
        assert invalid_target_stdout == ""
        assert (
            "Error: Thread target is not found or is not replyable: #dummy:missing"
            in invalid_target_stderr
        )
        assert "Code: SEND_FAILED" in invalid_target_stderr
        assert "Draft saved: yes" in invalid_target_stderr
        assert len(channel.send_attempts) == 1

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.QUEUED,
                value=ChannelDeliveryReceipt(provider_receipt_ref="queue-1"),
            )
        )
        queued_code, queued_stdout, queued_stderr = await run_bcc(
            node,
            runtime_session,
            ("message", "send", "--target", "#dummy:bcn-a"),
            body="queued reply",
        )
        assert queued_code == 0, queued_stderr
        assert queued_stderr == ""
        assert queued_stdout.startswith("Message queued to #dummy:bcn-a. Message ID: ")
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
            ("message", "send", "--target", "#dummy:bcn-a"),
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
            ("message", "send", "--target", "#dummy:bcn-a"),
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
            ("message", "send", "--target", "#dummy:bcn-a"),
            body="stale reply",
        )
        assert stale_code != 0
        assert stale_stdout == ""
        assert "Code: SEND_FRESH_CHECK_FAILED" in stale_stderr
        assert "Draft saved: yes" in stale_stderr
        assert len(channel.send_attempts) == 4
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
