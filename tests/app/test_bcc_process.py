from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.registry import AdapterFactories
from bazaar_compute_node.contrib.dummy import DummyAudit, DummyChannel, DummyRuntime
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import InboundMessage, RuntimeSession
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


async def wait_for_messages(node: NodeApplication, count: int) -> tuple[InboundMessage, ...]:
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
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    stdout, stderr = await process.communicate()
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
