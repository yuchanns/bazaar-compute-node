from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from bcn_test_support import RecordingAudit, TestChannel, TestRuntime

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.registry import AdapterFactories
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    InboundMessage,
    ReminderState,
    RuntimeSession,
)
from bazaar_compute_node.core.runtime import RuntimeCommandContext

ANCHOR_ID = "019c5678-0000-7000-8000-000000000001"


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

    return AdapterFactories(
        channel=lambda _context: TestChannel(),
        runtime=create_runtime,
        storage=SqliteDatabase,
        audit=RecordingAudit,
    )


def make_message() -> InboundMessage:
    return InboundMessage(
        seq=0,
        message_id=ANCHOR_ID,
        session_id="bcn-a",
        channel_session_id="channel-a",
        channel="test",
        provider_thread_id="provider-thread-a",
        provider_message_id="provider-message-a",
        received_at_ms=1_000,
        provider_time_ms=1_000,
        sender="sender",
        message_type="human",
        canonical_target="dm:channel-a",
        body="Please inspect this later",
    )


async def wait_for_runtime_session(node: NodeApplication) -> RuntimeSession:
    for _ in range(200):
        session = node.orchestrator.runtime_session("bcn-a")
        if session is not None:
            return session
        await asyncio.sleep(0.01)
    raise AssertionError("runtime session did not become live")


async def run_bcc(
    node: NodeApplication,
    runtime_session: RuntimeSession,
    arguments: tuple[str, ...],
) -> tuple[int, str, str]:
    wrapper_path = node._wrapper_path
    if wrapper_path is None:
        raise AssertionError("bcc wrapper was not installed")
    environment = dict(cast(dict[str, str], node._runtime_environment(runtime_session)))
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
async def test_bcc_reminder_commands_use_real_wrapper_ipc_and_sqlite(
    tmp_path: Path,
) -> None:
    node = NodeApplication(
        factories=make_factories(),
        endpoint_path=tmp_path / "bcn.sock",
        node_id="node-reminder",
        timeout_budget=make_budget(),
    )
    channel = cast(TestChannel, node.channel)
    await node.start()
    try:
        await channel.inject(make_message())
        runtime_session = await wait_for_runtime_session(node)

        code, stdout, stderr = await run_bcc(
            node,
            runtime_session,
            (
                "reminder",
                "schedule",
                "--title",
                "Inspect reminder",
                "--delay-seconds",
                "3600",
                "--message-id",
                ANCHOR_ID,
            ),
        )
        assert code == 0, stderr
        assert stderr == ""
        assert stdout.startswith("Reminder scheduled: #")
        assert '(one-time) "Inspect reminder"' in stdout
        assert "\nNext: " in stdout

        async with node.storage.transaction() as transaction:
            reminders = await transaction.list_reminders(
                "bcn-a",
                frozenset(ReminderState),
            )
        assert len(reminders) == 1
        reminder = reminders[0]
        assert reminder.timezone == "UTC"

        list_code, list_stdout, list_stderr = await run_bcc(
            node,
            runtime_session,
            ("reminder", "list"),
        )
        assert list_code == 0, list_stderr
        assert list_stderr == ""
        assert (
            f"#{reminder.reminder_id.replace('-', '')[:8]} [scheduled]" in list_stdout
        )
        assert f"anchor={ANCHOR_ID.replace('-', '')[:8]}" in list_stdout

        update_code, update_stdout, update_stderr = await run_bcc(
            node,
            runtime_session,
            (
                "reminder",
                "update",
                "--id",
                reminder.reminder_id,
                "--title",
                "Inspect updated reminder",
            ),
        )
        assert update_code == 0, update_stderr
        assert update_stderr == ""
        assert update_stdout.startswith("Reminder updated: #")

        snooze_code, snooze_stdout, snooze_stderr = await run_bcc(
            node,
            runtime_session,
            (
                "reminder",
                "snooze",
                "--id",
                reminder.reminder_id,
                "--by",
                "5m",
            ),
        )
        assert snooze_code == 0, snooze_stderr
        assert snooze_stderr == ""
        assert snooze_stdout.startswith("Reminder snoozed: #")

        cancel_code, cancel_stdout, cancel_stderr = await run_bcc(
            node,
            runtime_session,
            ("reminder", "cancel", "--id", reminder.reminder_id),
        )
        assert cancel_code == 0, cancel_stderr
        assert cancel_stderr == ""
        assert cancel_stdout.startswith("Reminder canceled: #")

        all_code, all_stdout, all_stderr = await run_bcc(
            node,
            runtime_session,
            ("reminder", "list", "--all"),
        )
        assert all_code == 0, all_stderr
        assert all_stderr == ""
        assert "[canceled]" in all_stdout

        empty_code, empty_stdout, empty_stderr = await run_bcc(
            node,
            runtime_session,
            ("reminder", "check"),
        )
        assert empty_code == 0, empty_stderr
        assert empty_stdout == "No pending reminders.\n"
        assert empty_stderr == ""

        due_code, _due_stdout, due_stderr = await run_bcc(
            node,
            runtime_session,
            (
                "reminder",
                "schedule",
                "--title",
                "Due reminder",
                "--delay-seconds",
                "1",
                "--tz",
                "UTC",
                "--message-id",
                ANCHOR_ID,
            ),
        )
        assert due_code == 0, due_stderr
        async with node.storage.transaction() as transaction:
            all_reminders = await transaction.list_reminders(
                "bcn-a",
                frozenset(ReminderState),
            )
        due_reminder = next(
            item for item in all_reminders if item.title == "Due reminder"
        )
        assert due_reminder.timezone == "UTC"

        for _ in range(400):
            async with node.storage.transaction() as transaction:
                pending = await transaction.count_pending_reminder_occurrences("bcn-a")
            if pending:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Reminder occurrence did not become pending")

        check_code, check_stdout, check_stderr = await run_bcc(
            node,
            runtime_session,
            ("reminder", "check"),
        )
        assert check_code == 0, check_stderr
        assert check_stderr == ""
        assert "[class=due id=" in check_stdout
        assert "target=dm:channel-a" in check_stdout
        assert f"anchor={ANCHOR_ID}" in check_stdout
        assert check_stdout.endswith("No more pending reminders.\n")

        again_code, again_stdout, again_stderr = await run_bcc(
            node,
            runtime_session,
            ("reminder", "check"),
        )
        assert again_code == 0, again_stderr
        assert again_stdout == "No pending reminders.\n"
        assert again_stderr == ""
    finally:
        await node.stop()
