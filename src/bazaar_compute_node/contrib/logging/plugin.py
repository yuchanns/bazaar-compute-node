from __future__ import annotations

from ...core.observability import AuditContext, IAudit
from .audit import LoggingAudit


def create_audit(context: AuditContext) -> IAudit:
    del context
    return LoggingAudit()


__all__ = ["create_audit"]
