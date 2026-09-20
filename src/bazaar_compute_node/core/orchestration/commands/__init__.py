"""The one command service, assembled from its families: what the runtime's
own `bcc` reaches through the local command server, and what an operator
reaches from outside."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...actor import Actors
from ...audit import AuditRecorder
from ...channel import IChannel
from ...command import ICommandService
from ...concurrency import IThreadConcurrency
from ...storage import IStorage
from ..delivery import OutboundDeliveryService
from .messages import MessageCommands, OutboundAttachmentResolver
from .reminders import ReminderCommandFailure, ReminderCommands
from .review import ReviewCommands


class CommandService(
    MessageCommands, ReminderCommands, ReviewCommands, ICommandService
):
    def __init__(
        self,
        *,
        actors: Actors,
        storage: IStorage,
        audit: AuditRecorder,
        concurrency: IThreadConcurrency,
        clock: Callable[[], int],
        channel: IChannel,
        delivery: OutboundDeliveryService,
        workspace: Callable[[], Path],
        poke: Callable[[], None],
        reminder_concurrency: IThreadConcurrency,
    ) -> None:
        super().__init__(
            actors=actors,
            storage=storage,
            audit=audit,
            concurrency=concurrency,
            clock=clock,
        )
        self._with_messages(channel=channel, delivery=delivery, workspace=workspace)
        self._with_reminders(poke=poke, reminder_concurrency=reminder_concurrency)


__all__ = [
    "CommandService",
    "OutboundAttachmentResolver",
    "ReminderCommandFailure",
]
