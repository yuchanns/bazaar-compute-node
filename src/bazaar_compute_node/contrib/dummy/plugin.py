from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ...core.channel import IChannel
from ...core.observability import IAudit
from ...core.runtime import IRuntime, RuntimeCommandContext
from ...core.storage import IStorage
from .audit import DummyAudit
from .channel import DummyChannel
from .control import ControlHandler, DummyControl
from .runtime import DummyRuntime
from .storage import DummyStorage


def create_channel() -> IChannel:
    return DummyChannel()


def create_runtime(context: RuntimeCommandContext) -> IRuntime:
    async def run_default_commands(bcn_session_id: str) -> None:
        commands: tuple[tuple[Sequence[str], str | None], ...] = (
            (("message", "check"), None),
            (("message", "read", "--target", f"#dummy:{bcn_session_id}"), None),
            (
                ("message", "send", "--target", f"#dummy:{bcn_session_id}"),
                f"dummy reply for {bcn_session_id}\n",
            ),
        )
        for arguments, body in commands:
            await context.run_command(bcn_session_id, arguments, body)

    return DummyRuntime(default_command_runner=run_default_commands)


def create_storage(_data_dir: Path) -> IStorage:
    return DummyStorage()


def create_audit() -> IAudit:
    return DummyAudit()


def create_control(context: Mapping[str, object]) -> ControlHandler:
    return DummyControl(context).handle


__all__ = [
    "create_audit",
    "create_channel",
    "create_control",
    "create_runtime",
    "create_storage",
]
