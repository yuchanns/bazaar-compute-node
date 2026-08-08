from __future__ import annotations

from ...core.observability import IAudit
from .audit import LoggingAudit


def create_audit() -> IAudit:
    return LoggingAudit()


__all__ = ["create_audit"]
