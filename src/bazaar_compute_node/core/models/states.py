from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum


class SessionRuntimeState(StrEnum):
    """Provider-neutral runtime lifecycle state for one BCN session."""

    CREATED = "created"
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    COMPACTION_STARTING = "compaction_starting"
    COMPACTING = "compacting"
    COMPACTION_COMPLETED = "compaction_completed"
    STOPPING = "stopping"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"


class ChannelTargetKind(StrEnum):
    DM = "dm"
    GROUP = "group"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class SessionRuntimeObservationSource(StrEnum):
    """Origin of one session-runtime observation."""

    SESSION = "session"
    CHANNEL = "channel"
    RUNTIME = "runtime"
    RECOVERY = "recovery"


class SessionRuntimeSignal(StrEnum):
    """Provider-neutral fact applied to one session runtime lifecycle."""

    START_REQUESTED = "start_requested"
    START_CONFIRMED = "start_confirmed"
    TURN_STARTED = "turn_started"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_CANCELLED = "turn_cancelled"
    WORKING_OBSERVED = "working_observed"
    COMPACTION_STARTED = "compaction_started"
    COMPACTION_IN_PROGRESS = "compaction_in_progress"
    COMPACTION_COMPLETED = "compaction_completed"
    STOP_REQUESTED = "stop_requested"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILE_REQUESTED = "reconcile_requested"
    RECONCILE_CONFIRMED = "reconcile_confirmed"


@dataclass(frozen=True, slots=True)
class SessionRuntimeObservation:
    """An immutable, provider-neutral session-runtime lifecycle observation."""

    source: SessionRuntimeObservationSource
    signal: SessionRuntimeSignal
    observed_at_ms: int
    error_kind: str | None = None
    error_message: str | None = None


SESSION_RUNTIME_OBSERVATION_TRANSITIONS: Mapping[
    SessionRuntimeState, Mapping[SessionRuntimeSignal, SessionRuntimeState]
] = {
    SessionRuntimeState.CREATED: {
        SessionRuntimeSignal.START_REQUESTED: SessionRuntimeState.STARTING,
        SessionRuntimeSignal.WORKING_OBSERVED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
    SessionRuntimeState.STARTING: {
        SessionRuntimeSignal.START_REQUESTED: SessionRuntimeState.STARTING,
        SessionRuntimeSignal.START_CONFIRMED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.WORKING_OBSERVED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
    SessionRuntimeState.IDLE: {
        SessionRuntimeSignal.START_CONFIRMED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.WORKING_OBSERVED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.TURN_STARTED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.TURN_COMPLETED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.TURN_FAILED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.TURN_CANCELLED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.COMPACTION_STARTED: SessionRuntimeState.COMPACTION_STARTING,
        SessionRuntimeSignal.COMPACTION_IN_PROGRESS: SessionRuntimeState.COMPACTING,
        SessionRuntimeSignal.COMPACTION_COMPLETED: SessionRuntimeState.COMPACTION_COMPLETED,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
        SessionRuntimeSignal.RECONCILE_CONFIRMED: SessionRuntimeState.IDLE,
    },
    SessionRuntimeState.WORKING: {
        SessionRuntimeSignal.WORKING_OBSERVED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.TURN_STARTED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.TURN_COMPLETED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.TURN_FAILED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.TURN_CANCELLED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.COMPACTION_STARTED: SessionRuntimeState.COMPACTION_STARTING,
        SessionRuntimeSignal.COMPACTION_IN_PROGRESS: SessionRuntimeState.COMPACTING,
        SessionRuntimeSignal.COMPACTION_COMPLETED: SessionRuntimeState.COMPACTION_COMPLETED,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
    SessionRuntimeState.COMPACTION_STARTING: {
        SessionRuntimeSignal.WORKING_OBSERVED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.COMPACTION_STARTED: SessionRuntimeState.COMPACTION_STARTING,
        SessionRuntimeSignal.COMPACTION_IN_PROGRESS: SessionRuntimeState.COMPACTING,
        SessionRuntimeSignal.COMPACTION_COMPLETED: SessionRuntimeState.COMPACTION_COMPLETED,
        SessionRuntimeSignal.TURN_STARTED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.TURN_COMPLETED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
    SessionRuntimeState.COMPACTING: {
        SessionRuntimeSignal.WORKING_OBSERVED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.COMPACTION_STARTED: SessionRuntimeState.COMPACTING,
        SessionRuntimeSignal.COMPACTION_IN_PROGRESS: SessionRuntimeState.COMPACTING,
        SessionRuntimeSignal.COMPACTION_COMPLETED: SessionRuntimeState.COMPACTION_COMPLETED,
        SessionRuntimeSignal.TURN_STARTED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.TURN_COMPLETED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
    SessionRuntimeState.COMPACTION_COMPLETED: {
        SessionRuntimeSignal.WORKING_OBSERVED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.COMPACTION_STARTED: SessionRuntimeState.COMPACTION_STARTING,
        SessionRuntimeSignal.COMPACTION_IN_PROGRESS: SessionRuntimeState.COMPACTING,
        SessionRuntimeSignal.COMPACTION_COMPLETED: SessionRuntimeState.COMPACTION_COMPLETED,
        SessionRuntimeSignal.TURN_STARTED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.TURN_COMPLETED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
    SessionRuntimeState.STOPPING: {
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
    SessionRuntimeState.FAILED: {
        SessionRuntimeSignal.START_REQUESTED: SessionRuntimeState.STARTING,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.TURN_FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.TURN_CANCELLED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
    SessionRuntimeState.UNKNOWN: {
        SessionRuntimeSignal.RECONCILE_REQUESTED: SessionRuntimeState.RECONCILING,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
    SessionRuntimeState.RECONCILING: {
        SessionRuntimeSignal.START_REQUESTED: SessionRuntimeState.STARTING,
        SessionRuntimeSignal.START_CONFIRMED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.WORKING_OBSERVED: SessionRuntimeState.WORKING,
        SessionRuntimeSignal.RECONCILE_REQUESTED: SessionRuntimeState.RECONCILING,
        SessionRuntimeSignal.RECONCILE_CONFIRMED: SessionRuntimeState.IDLE,
        SessionRuntimeSignal.STOP_REQUESTED: SessionRuntimeState.STOPPING,
        SessionRuntimeSignal.FAILED: SessionRuntimeState.FAILED,
        SessionRuntimeSignal.UNKNOWN: SessionRuntimeState.UNKNOWN,
    },
}


SESSION_RUNTIME_STATE_TRANSITIONS: Mapping[
    SessionRuntimeState, frozenset[SessionRuntimeState]
] = {
    current: frozenset(
        target for target in signal_targets.values() if target is not current
    )
    for current, signal_targets in SESSION_RUNTIME_OBSERVATION_TRANSITIONS.items()
}


def reduce_session_runtime_state(
    current: SessionRuntimeState,
    observation: SessionRuntimeObservation,
) -> SessionRuntimeState:
    """Reduce one session-runtime observation without mutating state or doing I/O."""

    target = SESSION_RUNTIME_OBSERVATION_TRANSITIONS.get(current, {}).get(
        observation.signal
    )
    if target is None:
        raise StateTransitionError("session_runtime", current, observation.signal)
    ensure_transition(
        "session_runtime",
        current,
        target,
        SESSION_RUNTIME_STATE_TRANSITIONS,
    )
    return target


class RuntimeTurnState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


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


class StreamEventKind(StrEnum):
    AGENT_MESSAGE_DELTA = "agent-message-delta"
    PLAN_DELTA = "plan-delta"
    REASONING_SUMMARY_DELTA = "reasoning-summary-delta"
    REASONING_TEXT_DELTA = "reasoning-text-delta"
    COMMAND_OUTPUT_DELTA = "command-output-delta"
    COMMAND_INTERACTION = "command-interaction"
    FILE_CHANGE_UPDATE = "file-change-update"
    TOOL_PROGRESS = "tool-progress"
    ITEM_PROGRESS = "item-progress"
    TURN_PROGRESS = "turn-progress"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class SenderKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class SystemMessageKind(StrEnum):
    REMINDER = "reminder"
    HANDOFF = "handoff"


class ReminderState(StrEnum):
    SCHEDULED = "scheduled"
    FIRED = "fired"
    CANCELED = "canceled"


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


REMINDER_TRANSITIONS: Mapping[ReminderState, frozenset[ReminderState]] = {
    ReminderState.SCHEDULED: frozenset({ReminderState.FIRED, ReminderState.CANCELED}),
    ReminderState.FIRED: frozenset({ReminderState.SCHEDULED}),
    ReminderState.CANCELED: frozenset(),
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
    RuntimeTurnState.UNKNOWN: frozenset(),
}

OUTBOUND_DELIVERY_TRANSITIONS: Mapping[
    OutboundDeliveryState, frozenset[OutboundDeliveryState]
] = {
    OutboundDeliveryState.PENDING: frozenset(
        {
            OutboundDeliveryState.QUEUED,
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
        }
    ),
    OutboundDeliveryState.QUEUED: frozenset(
        {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
        }
    ),
    OutboundDeliveryState.SENT: frozenset(),
    OutboundDeliveryState.PARTIAL: frozenset(),
    OutboundDeliveryState.FAILED: frozenset(),
    OutboundDeliveryState.UNKNOWN: frozenset(
        {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
        }
    ),
}

RUNTIME_EVENT_TRANSITIONS: Mapping[RuntimeEventState, frozenset[RuntimeEventState]] = {
    RuntimeEventState.STARTED: frozenset(
        {
            RuntimeEventState.COMPLETED,
            RuntimeEventState.FAILED,
            RuntimeEventState.CANCELLED,
            RuntimeEventState.UNKNOWN,
        }
    ),
    RuntimeEventState.COMPLETED: frozenset(),
    RuntimeEventState.FAILED: frozenset(),
    RuntimeEventState.CANCELLED: frozenset(),
    RuntimeEventState.UNKNOWN: frozenset(
        {RuntimeEventState.COMPLETED, RuntimeEventState.FAILED}
    ),
}
