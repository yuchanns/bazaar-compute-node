"""Persistent, agent-owned reminders and their shared frontier scheduler."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from copy import copy

from bazaar_contrib_audit import AUDIT
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_contrib_legacy_node_migrations import LEGACY_NODE_MIGRATIONS
from bazaar_contrib_messages import MESSAGES
from bazaar_contrib_schema import SCHEMA
from bazaar_contrib_storage import DB
from bazaar_contrib_timer import TIMER
from bazaar_core import Context, Contrib, ContribSpec, ServiceKey
lazy from bazaar_contrib_sidecar import CLIENT

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
lazy from .cli._client import ReminderClient
lazy from .cli.reminder import reminder as reminder_group

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
    "commands",
    "reminder",
    "reminder_entry",
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


@asynccontextmanager
async def cli_lifespan(ctx: Context):
    client = ctx.accept(CLIENT)
    timeout = client.timeout
    client.timeout = None
    try:
        group = copy(reminder_group)
        group.context_settings = {
            **group.context_settings,
            "obj": ReminderClient(client.context),
        }
        with ctx.accept(COMMANDS).register(group):
            yield
    finally:
        client.timeout = timeout


reminder = Contrib(
    accepts=[SCHEMA, LEGACY_NODE_MIGRATIONS, DB, MESSAGES, TIMER, AUDIT],
    offers=[REMINDER],
    lifespan=lifespan,
)
commands = Contrib(accepts=[CLIENT, COMMANDS], lifespan=cli_lifespan)
reminder_entry = CommandEntry(
    contrib="reminder-cli",
    help="Reminder operations",
    dependencies=(
        ContribSpec(
            contrib="sidecar-windows" if sys.platform == "win32" else "sidecar-unix",
            config={"endpoint": os.environ.get("BCN_ENDPOINT")},
        ),
    ),
)
