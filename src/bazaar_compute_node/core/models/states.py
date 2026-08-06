from __future__ import annotations

from collections.abc import Mapping
from enum import Enum, StrEnum


class ChannelSessionState(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class BcnSessionState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class RuntimeProcessState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"


class RuntimeTurnState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"


class OutboundDeliveryState(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


class FreshCheckState(StrEnum):
    REQUIRED = "required"
    PASSED = "passed"
    FAILED = "failed"


class RuntimeEventState(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class StateTransitionError(ValueError):
    def __init__(self, aggregate: str, current: Enum, target: Enum) -> None:
        self.aggregate = aggregate
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid {aggregate} state transition: {current.value} -> {target.value}"
        )


def ensure_transition[StateT: Enum](
    aggregate: str,
    current: StateT,
    target: StateT,
    transitions: Mapping[StateT, frozenset[StateT]],
) -> None:
    if current is target:
        return
    if target not in transitions.get(current, frozenset()):
        raise StateTransitionError(aggregate, current, target)


CHANNEL_SESSION_TRANSITIONS: Mapping[
    ChannelSessionState, frozenset[ChannelSessionState]
] = {
    ChannelSessionState.ACTIVE: frozenset({ChannelSessionState.CLOSED}),
    ChannelSessionState.CLOSED: frozenset(),
}

BCN_SESSION_TRANSITIONS: Mapping[BcnSessionState, frozenset[BcnSessionState]] = {
    BcnSessionState.CREATED: frozenset(
        {
            BcnSessionState.RUNNING,
            BcnSessionState.STOPPING,
            BcnSessionState.FAILED,
        }
    ),
    BcnSessionState.RUNNING: frozenset(
        {BcnSessionState.STOPPING, BcnSessionState.FAILED}
    ),
    BcnSessionState.STOPPING: frozenset(
        {BcnSessionState.STOPPED, BcnSessionState.FAILED}
    ),
    BcnSessionState.STOPPED: frozenset(),
    BcnSessionState.FAILED: frozenset(),
}

RUNTIME_PROCESS_TRANSITIONS: Mapping[
    RuntimeProcessState, frozenset[RuntimeProcessState]
] = {
    RuntimeProcessState.STARTING: frozenset(
        {
            RuntimeProcessState.RUNNING,
            RuntimeProcessState.STOPPING,
            RuntimeProcessState.FAILED,
            RuntimeProcessState.UNKNOWN,
        }
    ),
    RuntimeProcessState.RUNNING: frozenset(
        {
            RuntimeProcessState.STOPPING,
            RuntimeProcessState.FAILED,
            RuntimeProcessState.UNKNOWN,
        }
    ),
    RuntimeProcessState.STOPPING: frozenset(
        {
            RuntimeProcessState.STOPPED,
            RuntimeProcessState.FAILED,
            RuntimeProcessState.UNKNOWN,
        }
    ),
    RuntimeProcessState.STOPPED: frozenset(),
    RuntimeProcessState.FAILED: frozenset(),
    RuntimeProcessState.UNKNOWN: frozenset({RuntimeProcessState.RECONCILING}),
    RuntimeProcessState.RECONCILING: frozenset(
        {
            RuntimeProcessState.RUNNING,
            RuntimeProcessState.STOPPING,
            RuntimeProcessState.STOPPED,
            RuntimeProcessState.FAILED,
            RuntimeProcessState.UNKNOWN,
        }
    ),
}

RUNTIME_TURN_TRANSITIONS: Mapping[RuntimeTurnState, frozenset[RuntimeTurnState]] = {
    RuntimeTurnState.STARTING: frozenset(
        {
            RuntimeTurnState.RUNNING,
            RuntimeTurnState.FAILED,
            RuntimeTurnState.CANCELLED,
            RuntimeTurnState.UNKNOWN,
        }
    ),
    RuntimeTurnState.RUNNING: frozenset(
        {
            RuntimeTurnState.COMPLETED,
            RuntimeTurnState.FAILED,
            RuntimeTurnState.CANCELLED,
            RuntimeTurnState.UNKNOWN,
        }
    ),
    RuntimeTurnState.COMPLETED: frozenset(),
    RuntimeTurnState.FAILED: frozenset(),
    RuntimeTurnState.CANCELLED: frozenset(),
    RuntimeTurnState.UNKNOWN: frozenset({RuntimeTurnState.RECONCILING}),
    RuntimeTurnState.RECONCILING: frozenset(
        {
            RuntimeTurnState.COMPLETED,
            RuntimeTurnState.FAILED,
            RuntimeTurnState.CANCELLED,
            RuntimeTurnState.UNKNOWN,
        }
    ),
}

OUTBOUND_DELIVERY_TRANSITIONS: Mapping[
    OutboundDeliveryState, frozenset[OutboundDeliveryState]
] = {
    OutboundDeliveryState.DRAFT: frozenset(
        {OutboundDeliveryState.PENDING, OutboundDeliveryState.REJECTED}
    ),
    OutboundDeliveryState.PENDING: frozenset(
        {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
        }
    ),
    OutboundDeliveryState.SENT: frozenset(),
    OutboundDeliveryState.FAILED: frozenset(),
    OutboundDeliveryState.UNKNOWN: frozenset(
        {OutboundDeliveryState.SENT, OutboundDeliveryState.FAILED}
    ),
    OutboundDeliveryState.REJECTED: frozenset(),
}

FRESH_CHECK_TRANSITIONS: Mapping[FreshCheckState, frozenset[FreshCheckState]] = {
    FreshCheckState.REQUIRED: frozenset(
        {FreshCheckState.PASSED, FreshCheckState.FAILED}
    ),
    FreshCheckState.PASSED: frozenset(),
    FreshCheckState.FAILED: frozenset(),
}

RUNTIME_EVENT_TRANSITIONS: Mapping[RuntimeEventState, frozenset[RuntimeEventState]] = {
    RuntimeEventState.STARTED: frozenset(
        {
            RuntimeEventState.COMPLETED,
            RuntimeEventState.FAILED,
            RuntimeEventState.UNKNOWN,
        }
    ),
    RuntimeEventState.COMPLETED: frozenset(),
    RuntimeEventState.FAILED: frozenset(),
    RuntimeEventState.UNKNOWN: frozenset(
        {RuntimeEventState.COMPLETED, RuntimeEventState.FAILED}
    ),
}
