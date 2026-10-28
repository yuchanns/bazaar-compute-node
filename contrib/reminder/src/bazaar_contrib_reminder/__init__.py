"""Persistent, agent-owned reminders and their shared frontier scheduler."""

from __future__ import annotations

from contextlib import asynccontextmanager

from bazaar_contrib_audit import AUDIT
from bazaar_contrib_legacy_node_migrations import LEGACY_NODE_MIGRATIONS
from bazaar_contrib_messages import MESSAGES
from bazaar_contrib_schema import SCHEMA
from bazaar_contrib_sidecar import RemoteEntry
from bazaar_contrib_storage import DB
from bazaar_contrib_timer import TIMER
from bazaar_core import Context, Contrib, ServiceKey

from .commands import ReminderCommandFailure, ReminderCommands
from .models import OwnedReminder, Reminder, ReminderOwner, ReminderState
from .reminder import (
    ReminderCancelRequest,
    ReminderCancelResult,
    ReminderListRequest,
    ReminderListResult,
    ReminderScheduleRequest,
    ReminderScheduleResult,
    ReminderSnoozeRequest,
    ReminderSnoozeResult,
    ReminderUpdateRequest,
    ReminderUpdateResult,
)
from .scheduler import ReminderScheduler, resolve_reminder_anchor
from .schema import MODULE
from .service import Reminders

__all__ = [
    "REMINDER",
    "OwnedReminder",
    "Reminder",
    "ReminderCancelRequest",
    "ReminderCancelResult",
    "ReminderCommandFailure",
    "ReminderCommands",
    "ReminderListRequest",
    "ReminderListResult",
    "ReminderOwner",
    "ReminderScheduleRequest",
    "ReminderScheduleResult",
    "ReminderScheduler",
    "ReminderSnoozeRequest",
    "ReminderSnoozeResult",
    "ReminderState",
    "ReminderUpdateRequest",
    "ReminderUpdateResult",
    "Reminders",
    "reminder",
    "remote",
    "resolve_reminder_anchor",
]

REMINDER = ServiceKey[Reminders]("reminder")


@asynccontextmanager
async def lifespan(ctx: Context):
    async with ctx.accept(SCHEMA).register(MODULE):
        service = Reminders(
            database=ctx.accept(DB),
            messages=ctx.accept(MESSAGES),
            timer=ctx.accept(TIMER),
            audit=ctx.accept(AUDIT),
        )
        ctx.offer(REMINDER, service)
        try:
            yield
        finally:
            await service.stop()


reminder = Contrib(
    accepts=[SCHEMA, LEGACY_NODE_MIGRATIONS, DB, MESSAGES, TIMER, AUDIT],
    offers=[REMINDER],
    lifespan=lifespan,
)

remote = RemoteEntry(REMINDER, ("request",))
