from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields

from bazaar_contrib_audit import Audit, AuditContext, AuditError
from bazaar_contrib_messages import Actor
from bazaar_contrib_messages.correlation import CorrelationContext
from bazaar_contrib_messages.states import ErrorKind, RuntimeEventState
from pydantic import JsonValue, TypeAdapter

from .clock import now_ms


class ReminderAudit:
    """Publish Reminder decisions and occurrences through the shared audit service."""

    def __init__(self, audit: Audit):
        self._audit = audit

    async def append(
        self,
        *,
        event_name: str,
        state: RuntimeEventState,
        correlation: CorrelationContext,
        metadata: Mapping[str, object] | None = None,
        error_kind: ErrorKind | None = None,
        error_message: str | None = None,
    ) -> None:
        at_ms = now_ms()
        payload: dict[str, object] = {
            "event_name": event_name,
            "state": state.value,
            "created_at_ms": at_ms,
            "correlation": {
                f"{item.name}_id" if isinstance(value, Actor) else item.name: value.id
                if isinstance(value, Actor)
                else value
                for item in fields(correlation)
                if (value := getattr(correlation, item.name)) is not None
            },
            "metadata": dict(metadata or {}),
        }
        if error_kind is None and error_message is not None:
            payload["error_message"] = error_message
        await self._audit.publish(
            event_name,
            context=AuditContext(
                agent_id=correlation.node_id or "",
                channel=correlation.channel,
                thread_id=correlation.thread_id,
                message_id=correlation.outbound_message_id,
                session_id=correlation.runtime_session_id,
                turn_id=correlation.turn_id,
            ),
            payload=TypeAdapter(JsonValue).validate_python(payload),
            occurred_at_ms=at_ms,
            error=None
            if error_kind is None
            else AuditError(kind=error_kind.value, message=error_message),
        )
