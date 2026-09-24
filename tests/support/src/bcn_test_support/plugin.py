from __future__ import annotations

from collections.abc import Sequence
from importlib.metadata import EntryPoint
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


# the test plugins, as installed ones are declared; they are not declared in
# the package, or a node run from a checkout would offer them as real kinds
ENTRY_POINTS = tuple(
    EntryPoint(name="test", value=f"bcn_test_support.plugin:{target}", group=group)
    for group, target in (
        ("bazaar_compute_node.channels", "builder"),
        ("bazaar_compute_node.runtimes", "runtime_builder"),
        ("bazaar_compute_node.storages", "create_storage"),
        ("bazaar_compute_node.audits", "create_audit"),
    )
)


def install() -> None:
    """Have the node find the test plugins beside the installed ones, in
    this process only: the suite calls it once, and a node a test starts in
    a process of its own runs through `bcn_test_support.node`."""

    from bazaar_compute_node.app import registry

    installed = registry.entry_points

    def entry_points(*, group: str) -> list[EntryPoint]:
        return [
            *installed(group=group),
            *(entry for entry in ENTRY_POINTS if entry.group == group),
        ]

    registry.entry_points = entry_points


__all__ = [
    "ENTRY_POINTS",
    "StaticRuntimeBuilder",
    "availability",
    "builder",
    "create_audit",
    "create_storage",
    "install",
    "runtime_builder",
]
