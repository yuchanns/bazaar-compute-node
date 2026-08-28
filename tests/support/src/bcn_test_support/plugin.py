from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from bazaar_compute_node.core.observability import IAudit
from bazaar_compute_node.core.runtime import IRuntime, RuntimeCommandContext
from bazaar_compute_node.core.storage import IStorage

from .audit import RecordingAudit
from .channel import StaticChannelBuilder
from .reminder_storage import MemoryStorage
from .runtime import TestRuntime

builder = StaticChannelBuilder()


def create_runtime(context: RuntimeCommandContext) -> IRuntime:
    async def run_default_commands(session_id: str) -> None:
        commands: tuple[tuple[Sequence[str], str | None], ...] = (
            (("message", "check"), None),
            (("message", "read", "--target", f"#test:{session_id}"), None),
            (
                ("message", "send", "--target", f"#test:{session_id}"),
                f"test reply for {session_id}\n",
            ),
        )
        for arguments, body in commands:
            await context.run_command(session_id, arguments, body)

    return TestRuntime(default_command_runner=run_default_commands)


def create_storage() -> IStorage:
    return cast(IStorage, MemoryStorage())


def create_audit() -> IAudit:
    return RecordingAudit()


__all__ = [
    "builder",
    "create_audit",
    "create_runtime",
    "create_storage",
]
