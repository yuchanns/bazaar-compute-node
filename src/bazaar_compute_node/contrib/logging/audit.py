from __future__ import annotations

import json
import logging
from dataclasses import fields

from ...core.audit import AuditEvent
from ...core.observability import IAudit, LogLevel

_LOG_LEVELS = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
}


class LoggingAudit(IAudit):
    """Emit sanitized audit events through the process logging pipeline."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        if logger is None:
            logger = logging.getLogger("bazaar_compute_node.audit")
            if not logger.handlers:
                logger.addHandler(logging.StreamHandler())
            logger.setLevel(logging.INFO)
            logger.propagate = False
        self._logger = logger

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        del timeout
        correlation = {
            field.name: value
            for field in fields(event.correlation)
            if (value := getattr(event.correlation, field.name)) is not None
        }
        payload: dict[str, object] = {
            "event_name": event.event_name,
            "state": event.state.value,
            "created_at_ms": event.created_at_ms,
            "correlation": correlation,
            "metadata": dict(event.metadata),
        }
        for key, value in (
            ("duration_ms", event.duration_ms),
            ("error_kind", event.error_kind.value if event.error_kind else None),
            ("error_type", event.error_type),
            ("error_message", event.error_message),
            ("traceback_ref", event.traceback_ref),
        ):
            if value is not None:
                payload[key] = value
        self._logger.log(
            _LOG_LEVELS[event.level],
            "%s",
            json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str),
        )
