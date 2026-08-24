from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .command import (
    InboxListResult,
    MessageCheckResult,
    MessageDraft,
    MessageReadResult,
    MessageSendResult,
)
from .handoff import HandoffCheckResult
from .inbox import InboxTargetPage
from .lifecycle import IAsyncLifecycle
from .models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    Handoff,
    InboundMessage,
    OutboundMessage,
    OwnedReminder,
    OwnedReminderOccurrence,
    Reminder,
    ReminderOccurrence,
    ReminderOwner,
    ReminderState,
    RuntimeAttempt,
)
from .reminder import ReminderCheckResult


class InboxTargetResolutionError(ValueError):
    """A target does not resolve to exactly one Agent-owned BCN session."""


class HandoffConflictError(ValueError):
    """A handoff command ID is already associated with another payload."""


@dataclass(frozen=True, slots=True)
class RecordInboundResult:
    channel_session: ChannelSession
    bcn_session: BcnSession
    message: InboundMessage
    channel_session_created: bool
    bcn_session_created: bool
    message_created: bool


@dataclass(frozen=True, slots=True)
class ReadMessageHistoryResult:
    source_session: BcnSession
    history: MessageReadResult


@dataclass(frozen=True, slots=True)
class PrepareOutboundResult:
    channel_session: ChannelSession
    target_session: BcnSession
    reply_to_provider_message_id: str | None
    outcome: MessageSendResult


@dataclass(frozen=True, slots=True)
class ReminderWakeResult:
    occurrence: ReminderOccurrence
    channel_session: ChannelSession
    bcn_session: BcnSession
    anchor_message: InboundMessage


@dataclass(frozen=True, slots=True)
class HandoffWakeResult:
    channel_session: ChannelSession
    bcn_session: BcnSession
    anchor_message: InboundMessage


class _StorageOperations(Protocol):
    """Storage operations exposed without implementation-specific transactions."""

    async def record_inbound(
        self,
        message: InboundMessage,
        *,
        now_ms: int,
    ) -> RecordInboundResult: ...

    async def check_messages(
        self,
        session_id: str,
        *,
        checked_at_ms: int,
    ) -> MessageCheckResult: ...

    async def read_message_history(
        self,
        caller_session_id: str,
        *,
        target: str,
        around_message_id: str | None,
        limit: int,
    ) -> ReadMessageHistoryResult: ...

    async def read_inbox_catalog(
        self,
        caller_session_id: str,
        *,
        limit: int,
        offset: int,
    ) -> InboxListResult: ...

    async def prepare_outbound(
        self,
        caller_session_id: str,
        *,
        command_id: str,
        payload: MessageDraft,
        attempted_at_ms: int,
        draft_replaced: bool,
    ) -> PrepareOutboundResult: ...

    async def check_reminders(
        self,
        session_id: str,
        *,
        limit: int,
        read_at_ms: int,
    ) -> ReminderCheckResult: ...

    async def load_reminder_wake(
        self,
        session_id: str,
    ) -> ReminderWakeResult | None: ...

    async def load_handoff_wake(
        self,
        session_id: str,
    ) -> HandoffWakeResult | None: ...

    async def find_channel_session(
        self, *, channel: str, provider_thread_id: str
    ) -> ChannelSession | None: ...

    async def get_channel_session(self, session_id: str) -> ChannelSession | None: ...
    async def get_bcn_session(self, session_id: str) -> BcnSession | None: ...
    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None: ...
    async def get_runtime_attempt(self, turn_id: str) -> RuntimeAttempt | None: ...
    async def get_consumer_cursor(self, session_id: str) -> ConsumerCursor | None: ...
    async def get_latest_inbound_seq(self, session_id: str) -> int: ...
    async def get_latest_inbound_message(
        self, session_id: str
    ) -> InboundMessage | None: ...

    async def count_inbound_messages(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
    ) -> int: ...

    async def list_inbox_targets(
        self, *, limit: int = 100, offset: int = 0
    ) -> InboxTargetPage: ...

    async def resolve_inbox_target(self, target: str) -> BcnSession: ...

    async def find_inbound_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
    ) -> InboundMessage | None: ...

    async def resolve_inbound_message(
        self, session_id: str, message_id: str
    ) -> InboundMessage | None: ...

    async def list_ready_attachment_paths(self) -> tuple[str, ...]: ...

    async def list_inbound_messages(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        around_message_id: str | None = None,
        notifying_only: bool = False,
        latest: bool = False,
        limit: int = 100,
    ) -> tuple[InboundMessage, ...]: ...

    async def save_channel_session(self, session: ChannelSession) -> None: ...
    async def save_bcn_session(self, session: BcnSession) -> None: ...
    async def save_runtime_attempt(self, attempt: RuntimeAttempt) -> None: ...
    async def append_inbound_message(
        self, message: InboundMessage
    ) -> InboundMessage: ...
    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None: ...

    async def get_outbound_message(
        self, outbound_message_id: str
    ) -> OutboundMessage | None: ...

    async def save_outbound_message(
        self, message: OutboundMessage
    ) -> OutboundMessage: ...

    async def get_reminder(
        self, owner_session_id: str, reminder_id: str
    ) -> Reminder | None: ...

    async def list_reminders(
        self,
        owner_session_id: str,
        statuses: frozenset[ReminderState],
    ) -> tuple[Reminder, ...]: ...

    async def save_new_reminder(self, reminder: Reminder) -> Reminder: ...

    async def save_reminder_transition(
        self, expected_revision: int, reminder: Reminder
    ) -> Reminder: ...

    async def get_next_scheduled_reminder(self) -> Reminder | None: ...

    async def list_due_reminders(
        self, now_ms: int, *, limit: int
    ) -> tuple[Reminder, ...]: ...

    async def save_fired_occurrence(
        self,
        expected_revision: int,
        reminder: Reminder,
        occurrence: ReminderOccurrence,
    ) -> ReminderOccurrence: ...

    async def list_pending_reminder_occurrences(
        self, owner_session_id: str, *, limit: int
    ) -> tuple[ReminderOccurrence, ...]: ...

    async def count_pending_reminder_occurrences(
        self, owner_session_id: str
    ) -> int: ...

    async def mark_reminder_occurrences_read(
        self,
        owner_session_id: str,
        occurrence_ids: tuple[str, ...],
        *,
        read_at_ms: int,
    ) -> tuple[ReminderOccurrence, ...]: ...

    async def list_sessions_with_pending_reminders(self) -> tuple[str, ...]: ...

    async def get_owned_reminder(
        self,
        agent_id: str,
        owner_session_id: str,
        reminder_id: str,
    ) -> OwnedReminder | None: ...

    async def get_next_scheduled_owned_reminder(self) -> OwnedReminder | None: ...

    async def list_due_owned_reminders(
        self,
        now_ms: int,
        *,
        limit: int,
    ) -> tuple[OwnedReminder, ...]: ...

    async def save_owned_fired_occurrence(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
        occurrence: OwnedReminderOccurrence,
    ) -> OwnedReminderOccurrence: ...

    async def list_pending_reminder_owners(self) -> tuple[ReminderOwner, ...]: ...


class IStorage(IAsyncLifecycle, _StorageOperations, Protocol):
    """Node-owned durable storage lifecycle and Agent scope factory."""

    @property
    def name(self) -> str: ...

    def scope(self, agent_id: str, agent_name: str) -> IStorageScope: ...


class IStorageScope(IStorage, Protocol):
    """Immutable Agent view over shared storage.

    Scope lifecycle methods are intentionally no-op at the adapter boundary; the
    Node-owned storage remains the only object that starts or stops physical storage.
    """

    @property
    def agent_id(self) -> str: ...

    @property
    def agent_name(self) -> str: ...


class IHandoffStorageScope(IStorageScope, Protocol):
    """Agent storage scope with handoff operations."""

    async def check_handoffs(
        self,
        session_id: str,
        *,
        limit: int,
        read_at_ms: int,
    ) -> HandoffCheckResult: ...

    async def save_handoff(self, handoff: Handoff) -> Handoff: ...

    async def list_pending_handoffs(
        self, target_session_id: str, *, limit: int
    ) -> tuple[Handoff, ...]: ...

    async def count_pending_handoffs(self, target_session_id: str) -> int: ...

    async def mark_handoffs_read(
        self,
        target_session_id: str,
        handoff_ids: tuple[str, ...],
        *,
        read_at_ms: int,
    ) -> tuple[Handoff, ...]: ...
