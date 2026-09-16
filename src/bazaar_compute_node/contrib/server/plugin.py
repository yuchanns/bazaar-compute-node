from __future__ import annotations

from ...core.observability import AuditContext, IAudit
from .audit import ServerAudit


def create_audit(context: AuditContext) -> IAudit:
    return ServerAudit(context.options, timeout_budget=context.timeout_budget)


__all__ = ["create_audit"]
