from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from bazaar_compute_node.core.observability import AuditContext, IAudit
from bazaar_compute_node.core.runtime import (
    IRuntime,
    IRuntimeBuilder,
    RuntimeAvailability,
    RuntimeCommandContext,
    RuntimeModel,
    RuntimeModels,
)
from bazaar_compute_node.core.storage import IStorage

from .audit import RecordingAudit
from .channel import StaticChannelBuilder
from .reminder_storage import MemoryStorage
from .runtime import TestRuntime

builder = StaticChannelBuilder()

# what this runtime says of itself when a node asks what it could run
availability = RuntimeAvailability(kind="test", available=True, version="1.2.3")
listed = RuntimeModels(
    models=(RuntimeModel(id="test-model", name="Test Model", efforts=("low", "high")),)
)


class StaticRuntimeBuilder(IRuntimeBuilder):
    def build(self, context: RuntimeCommandContext) -> IRuntime:
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

    async def inspect(self, *, timeout: float) -> RuntimeAvailability:
        del timeout
        return availability

    async def models(self, *, timeout: float) -> RuntimeModels:
        del timeout
        return listed


runtime_builder = StaticRuntimeBuilder()


def create_storage() -> IStorage:
    return cast(IStorage, MemoryStorage())


def create_audit(context: AuditContext) -> IAudit:
    return RecordingAudit(options=context.options)


__all__ = [
    "StaticRuntimeBuilder",
    "availability",
    "builder",
    "create_audit",
    "create_storage",
    "runtime_builder",
]
