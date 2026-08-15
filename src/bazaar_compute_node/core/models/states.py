from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum


class AgentState(StrEnum):
    """Provider-neutral lifecycle state for one bcn agent session."""

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


class AgentTickSource(StrEnum):
    """Origin of an observation; the orchestrator remains the state writer."""

    SESSION = "session"
    CHANNEL = "channel"
    RUNTIME = "runtime"
    RECOVERY = "recovery"


class AgentSignal(StrEnum):
    """Provider-neutral facts that the core reducer can apply to an agent."""

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
class AgentTick:
    """An immutable, provider-neutral lifecycle observation."""

    source: AgentTickSource
    signal: AgentSignal
    observed_at_ms: int
    error_kind: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, AgentTickSource):
            raise TypeError("agent tick source is invalid")
        if not isinstance(self.signal, AgentSignal):
            raise TypeError("agent tick signal is invalid")
        if not isinstance(self.observed_at_ms, int) or self.observed_at_ms < 0:
            raise ValueError("observed_at_ms must be a non-negative integer")
        if self.error_kind is not None and (
            not isinstance(self.error_kind, str) or not self.error_kind
        ):
            raise ValueError("error_kind must be a non-empty string when provided")
        if self.error_message is not None and not isinstance(self.error_message, str):
            raise TypeError("error_message must be a string when provided")


AGENT_TICK_TRANSITIONS: Mapping[AgentState, Mapping[AgentSignal, AgentState]] = {
    AgentState.CREATED: {
        AgentSignal.START_REQUESTED: AgentState.STARTING,
        AgentSignal.WORKING_OBSERVED: AgentState.WORKING,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
    AgentState.STARTING: {
        AgentSignal.START_REQUESTED: AgentState.STARTING,
        AgentSignal.START_CONFIRMED: AgentState.IDLE,
        AgentSignal.WORKING_OBSERVED: AgentState.WORKING,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
    AgentState.IDLE: {
        AgentSignal.START_CONFIRMED: AgentState.IDLE,
        AgentSignal.WORKING_OBSERVED: AgentState.WORKING,
        AgentSignal.TURN_STARTED: AgentState.WORKING,
        AgentSignal.TURN_COMPLETED: AgentState.IDLE,
        AgentSignal.TURN_FAILED: AgentState.IDLE,
        AgentSignal.TURN_CANCELLED: AgentState.IDLE,
        AgentSignal.COMPACTION_STARTED: AgentState.COMPACTION_STARTING,
        AgentSignal.COMPACTION_IN_PROGRESS: AgentState.COMPACTING,
        AgentSignal.COMPACTION_COMPLETED: AgentState.COMPACTION_COMPLETED,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
        AgentSignal.RECONCILE_CONFIRMED: AgentState.IDLE,
    },
    AgentState.WORKING: {
        AgentSignal.WORKING_OBSERVED: AgentState.WORKING,
        AgentSignal.TURN_STARTED: AgentState.WORKING,
        AgentSignal.TURN_COMPLETED: AgentState.IDLE,
        AgentSignal.TURN_FAILED: AgentState.IDLE,
        AgentSignal.TURN_CANCELLED: AgentState.IDLE,
        AgentSignal.COMPACTION_STARTED: AgentState.COMPACTION_STARTING,
        AgentSignal.COMPACTION_IN_PROGRESS: AgentState.COMPACTING,
        AgentSignal.COMPACTION_COMPLETED: AgentState.COMPACTION_COMPLETED,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
    AgentState.COMPACTION_STARTING: {
        AgentSignal.WORKING_OBSERVED: AgentState.WORKING,
        AgentSignal.COMPACTION_STARTED: AgentState.COMPACTION_STARTING,
        AgentSignal.COMPACTION_IN_PROGRESS: AgentState.COMPACTING,
        AgentSignal.COMPACTION_COMPLETED: AgentState.COMPACTION_COMPLETED,
        AgentSignal.TURN_STARTED: AgentState.WORKING,
        AgentSignal.TURN_COMPLETED: AgentState.IDLE,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
    AgentState.COMPACTING: {
        AgentSignal.WORKING_OBSERVED: AgentState.WORKING,
        AgentSignal.COMPACTION_STARTED: AgentState.COMPACTING,
        AgentSignal.COMPACTION_IN_PROGRESS: AgentState.COMPACTING,
        AgentSignal.COMPACTION_COMPLETED: AgentState.COMPACTION_COMPLETED,
        AgentSignal.TURN_STARTED: AgentState.WORKING,
        AgentSignal.TURN_COMPLETED: AgentState.IDLE,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
    AgentState.COMPACTION_COMPLETED: {
        AgentSignal.WORKING_OBSERVED: AgentState.WORKING,
        AgentSignal.COMPACTION_STARTED: AgentState.COMPACTION_STARTING,
        AgentSignal.COMPACTION_IN_PROGRESS: AgentState.COMPACTING,
        AgentSignal.COMPACTION_COMPLETED: AgentState.COMPACTION_COMPLETED,
        AgentSignal.TURN_STARTED: AgentState.WORKING,
        AgentSignal.TURN_COMPLETED: AgentState.IDLE,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
    AgentState.STOPPING: {
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
    AgentState.FAILED: {
        AgentSignal.START_REQUESTED: AgentState.STARTING,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.TURN_FAILED: AgentState.FAILED,
        AgentSignal.TURN_CANCELLED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
    AgentState.UNKNOWN: {
        AgentSignal.RECONCILE_REQUESTED: AgentState.RECONCILING,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
    AgentState.RECONCILING: {
        AgentSignal.START_REQUESTED: AgentState.STARTING,
        AgentSignal.START_CONFIRMED: AgentState.IDLE,
        AgentSignal.WORKING_OBSERVED: AgentState.WORKING,
        AgentSignal.RECONCILE_REQUESTED: AgentState.RECONCILING,
        AgentSignal.RECONCILE_CONFIRMED: AgentState.IDLE,
        AgentSignal.STOP_REQUESTED: AgentState.STOPPING,
        AgentSignal.FAILED: AgentState.FAILED,
        AgentSignal.UNKNOWN: AgentState.UNKNOWN,
    },
}


AGENT_STATE_TRANSITIONS: Mapping[AgentState, frozenset[AgentState]] = {
    current: frozenset(
        target for target in signal_targets.values() if target is not current
    )
    for current, signal_targets in AGENT_TICK_TRANSITIONS.items()
}


def reduce_agent_tick(current: AgentState, tick: AgentTick) -> AgentState:
    """Reduce one tick without mutating state or performing I/O."""

    if not isinstance(current, AgentState):
        raise TypeError("agent state is invalid")
    if not isinstance(tick, AgentTick):
        raise TypeError("agent tick is invalid")
    target = AGENT_TICK_TRANSITIONS.get(current, {}).get(tick.signal)
    if target is None:
        raise StateTransitionError("agent", current, tick.signal)
    ensure_transition("agent", current, target, AGENT_STATE_TRANSITIONS)
    return target


class RuntimeTurnState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class OutboundDeliveryState(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    QUEUED = "queued"
    SENT = "sent"
    PARTIAL = "partial"
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
    ReminderState.SCHEDULED: frozenset(
        {ReminderState.FIRED, ReminderState.CANCELED}
    ),
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
    OutboundDeliveryState.DRAFT: frozenset(
        {OutboundDeliveryState.PENDING, OutboundDeliveryState.REJECTED}
    ),
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
