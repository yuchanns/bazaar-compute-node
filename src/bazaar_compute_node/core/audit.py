from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .correlation import CorrelationContext
from .models import RuntimeEventState
from .observability import LogLevel
from .sanitization import is_sensitive_field


class ErrorKind(StrEnum):
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    SESSION_NOT_FOUND = "session_not_found"
    TARGET_NOT_REPLYABLE = "target_not_replyable"
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


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Sanitized append-only event contract shared by all adapters."""

    event_name: str
    state: RuntimeEventState
    created_at_ms: int
    correlation: CorrelationContext
    level: LogLevel = LogLevel.INFO
    duration_ms: int | None = None
    error_kind: ErrorKind | None = None
    error_type: str | None = None
    error_message: str | None = None
    traceback_ref: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_name, str) or not self.event_name:
            raise ValueError("event_name must be a non-empty string")
        if self.error_kind is None and any(
            value is not None
            for value in (
                self.error_type,
                self.error_message,
                self.traceback_ref,
            )
        ):
            raise ValueError("error details require an error_kind")
        for value, field_name in (
            (self.error_type, "error_type"),
            (self.error_message, "error_message"),
            (self.traceback_ref, "traceback_ref"),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(
                    f"{field_name} must be a non-empty string when present"
                )
        pending: list[object] = [self.metadata]
        while pending:
            value = pending.pop()
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise TypeError("audit metadata keys must be strings")
                    if is_sensitive_field(key):
                        raise ValueError(
                            f"audit metadata cannot contain sensitive field: {key}"
                        )
                    pending.append(item)
            elif isinstance(value, list | tuple):
                pending.extend(value)
