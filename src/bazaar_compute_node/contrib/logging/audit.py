from __future__ import annotations

import json
import logging
from collections.abc import Mapping

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

    @property
    def name(self) -> str:
        return "logging"

    def __init__(self, logger: logging.Logger | None = None) -> None:
        if logger is None:
            logger = logging.getLogger("bazaar_compute_node.audit")
            if not logger.handlers:
                logger.addHandler(logging.StreamHandler())
            logger.setLevel(logging.INFO)
            logger.propagate = False
        self._logger = logger

    @property
    def health(self) -> Mapping[str, object]:
        return {}

    async def start(self, *, timeout: float) -> None:
        del timeout

    async def stop(self, *, timeout: float) -> None:
        del timeout

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        del timeout
        self._logger.log(
            _LOG_LEVELS[event.level],
            "%s",
            json.dumps(
                event.as_payload(), separators=(",", ":"), sort_keys=True, default=str
            ),
        )
