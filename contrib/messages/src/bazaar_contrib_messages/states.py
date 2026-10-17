from __future__ import annotations

from enum import StrEnum


class ChannelTargetKind(StrEnum):
    DM = "dm"
    GROUP = "group"


class Review(StrEnum):
    """Whether whoever is behind a conversation may talk to the agent:
    not yet decided, allowed, or turned away."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


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


class SenderKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class SystemMessageKind(StrEnum):
    REMINDER = "reminder"


class ErrorKind(StrEnum):
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    SESSION_NOT_FOUND = "session_not_found"
    EMPTY_BODY = "empty_body"
    FRESH_CHECK_REQUIRED = "fresh_check_required"
    FRESH_CHECK_FAILED = "fresh_check_failed"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_PARTIAL = "provider_partial"
    PROVIDER_UNKNOWN = "provider_unknown"
    PROTOCOL = "protocol"
    STORAGE = "storage"
    SHUTDOWN_TIMEOUT = "shutdown_timeout"
    INTERNAL = "internal"
