from __future__ import annotations

from collections.abc import Mapping, Sequence

from bazaar_compute_node.app.command import ControlHandler
from bazaar_compute_node.core.channel import IChannel
from bazaar_compute_node.core.observability import IAudit
from bazaar_compute_node.core.runtime import IRuntime, RuntimeCommandContext
from bazaar_compute_node.core.storage import IStorage

from .audit import RecordingAudit
from .channel import TestChannel
from .control import TestControl
from .runtime import TestRuntime
from .storage import MemoryStorage


def create_channel() -> IChannel:
    return TestChannel()


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
    return MemoryStorage()


def create_audit() -> IAudit:
    return RecordingAudit()


def create_control(context: Mapping[str, object]) -> ControlHandler:
    return TestControl(context).handle


__all__ = [
    "create_audit",
    "create_channel",
    "create_control",
    "create_runtime",
    "create_storage",
]
