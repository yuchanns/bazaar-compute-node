from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from bazaar_contrib_messages import Agent, Messages, Thread
from bazaar_contrib_messages.models import (
    InboundAttachment,
    Message,
    OutboundAttachment,
)
from bazaar_contrib_messages.operations import UnreadMessageOwner
from bazaar_contrib_messages.states import MessageDirection
from bazaar_contrib_storage import Database, Session

from .models import OwnedReminder, Reminder, ReminderState
from .repository import Repository


class ReminderStore:
    """Reminder reads, writes and atomic occurrence materialization."""

    def __init__(
        self, database: Database, messages: Messages, owner: Agent | None = None
    ):
        self.database = database
        self.messages = messages
        self.owner = owner

    def for_agent(self, owner: Agent) -> ReminderStore:
        return ReminderStore(self.database, self.messages, owner)

    async def _run[T](
        self,
        operation: Callable[[Repository], Awaitable[T]],
        *,
        mode: Literal["read", "write", "transaction"] = "read",
    ) -> T:
        async def run(session: Session) -> T:
            return await operation(Repository(session, self.messages, self.owner))

        if mode == "read":
            return await self.database.read(run)
        return await self.database.write(run, transactional=mode == "transaction")

    async def get_reminder(self, owner: Thread, reminder_id: str) -> Reminder | None:
        return await self._run(
            lambda repository: repository.get_reminder(owner, reminder_id)
        )

    async def list_reminders(
        self, owner: Thread, statuses: frozenset[ReminderState]
    ) -> tuple[Reminder, ...]:
        return await self._run(
            lambda repository: repository.list_reminders(owner, statuses)
        )

    async def save_new_reminder(self, reminder: Reminder) -> Reminder:
        return await self._run(
            lambda repository: repository.save_new_reminder(reminder), mode="write"
        )

    async def save_reminder_transition(
        self, expected_revision: int, reminder: Reminder
    ) -> Reminder:
        return await self._run(
            lambda repository: repository.save_reminder_transition(
                expected_revision, reminder
            ),
            mode="write",
        )

    async def get_owned_reminder(
        self, owner: Agent, thread: Thread, reminder_id: str
    ) -> OwnedReminder | None:
        return await self._run(
            lambda repository: repository.get_owned_reminder(owner, thread, reminder_id)
        )

    async def get_next_scheduled_owned_reminder(self) -> OwnedReminder | None:
        return await self._run(
            lambda repository: repository.get_next_scheduled_owned_reminder()
        )

    async def list_due_owned_reminders(
        self, now_ms: int, *, limit: int
    ) -> tuple[OwnedReminder, ...]:
        return await self._run(
            lambda repository: repository.list_due_owned_reminders(now_ms, limit=limit)
        )

    async def save_owned_reminder_transition(
        self, expected_revision: int, reminder: OwnedReminder
    ) -> Reminder | None:
        return await self._run(
            lambda repository: repository.save_owned_reminder_transition(
                expected_revision, reminder
            ),
            mode="write",
        )

    async def materialize_owned_reminder_message(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
        system_message: Message[InboundAttachment],
    ) -> Message[InboundAttachment] | None:
        return await self._run(
            lambda repository: repository.materialize_owned_reminder_message(
                expected_revision, reminder, system_message
            ),
            mode="transaction",
        )

    async def list_unread_message_owners(self) -> tuple[UnreadMessageOwner, ...]:
        return await self.messages.list_unread_message_owners()

    async def list_thread_ids(self) -> tuple[Thread, ...]:
        if self.owner is None:
            raise RuntimeError("Agent-owned message read requires an Agent scope")
        return await self.messages.storage(self.owner).list_thread_ids()

    async def resolve_message(
        self,
        actor: Thread,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        if self.owner is None:
            raise RuntimeError("Agent-owned message read requires an Agent scope")
        return await self.messages.storage(self.owner).resolve_message(
            actor, message_id, direction=direction
        )

    async def get_owned_message(
        self,
        owner: Agent,
        actor: Thread,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        return await self.messages.storage(owner).get_owned_message(
            owner, actor, message_id, direction=direction
        )
