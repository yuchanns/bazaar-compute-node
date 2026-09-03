from __future__ import annotations

from enum import StrEnum


class ChannelTargetKind(StrEnum):
    DM = "dm"
    GROUP = "group"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class RuntimeTurnState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class OutboundDeliveryState(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    SENT = "sent"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RuntimeEventState(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class SenderKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class SystemMessageKind(StrEnum):
    REMINDER = "reminder"
    HANDOFF = "handoff"


class ReminderState(StrEnum):
    SCHEDULED = "scheduled"
    FIRED = "fired"
    CANCELED = "canceled"
