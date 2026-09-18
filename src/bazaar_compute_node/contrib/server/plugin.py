from __future__ import annotations

from ...core.control import ControlContext, IControl
from ...core.observability import AuditContext, IAudit
from .audit import ServerAudit
from .control import ServerControl


def create_audit(context: AuditContext) -> IAudit:
    return ServerAudit(context.options, timeout_budget=context.timeout_budget)


def create_control(context: ControlContext) -> IControl:
    return ServerControl(context)


__all__ = ["create_audit", "create_control"]
