from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..i18n import Translator
from .models import (
    ContentDelta,
    ContextCompactionCompleted,
    ContextCompactionStarted,
    RuntimeEventPayload,
    RuntimeOutputEvent,
    TokenUsage,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallInteraction,
    ToolCallPatchUpdated,
    ToolCallStarted,
    ToolCallTextDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    TurnUnknown,
    UsageUpdated,
)


class ActivityKind(StrEnum):
    TOOL_CALL = "tool_call"
    CONTEXT_COMPACTION = "context_compaction"


class ActivityStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ActivityOutcome(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


_MAX_ERROR_MESSAGE = 1_000
_TERMINAL_OUTCOMES = {
    TurnCompleted: ActivityOutcome.COMPLETED,
    TurnFailed: ActivityOutcome.FAILED,
    TurnCancelled: ActivityOutcome.CANCELLED,
    TurnUnknown: ActivityOutcome.UNKNOWN,
}
_STATUS_ICONS = {
    ActivityStatus.RUNNING: "⌛️",
    ActivityStatus.COMPLETED: "✅",
    ActivityStatus.FAILED: "❌",
}


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    kind: ActivityKind
    status: ActivityStatus
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityOverview:
    outcome: ActivityOutcome = ActivityOutcome.COMPLETED
    error_message: str | None = None
    tool_calls: int = 0
    context_compactions: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def empty(self) -> bool:
        return not (
            self.error_message
            or self.tool_calls
            or self.context_compactions
            or self.input_tokens
            or self.cached_input_tokens
            or self.output_tokens
        )


@dataclass(frozen=True, slots=True)
class ActivityLine:
    icon: str
    label: str
    name: str | None = None


@dataclass(slots=True)
class ActivityReducer:
    snapshot: ActivitySnapshot | None = None
    overview: ActivityOverview | None = None
    tool_call_ids: set[str] = field(default_factory=set)
    compaction_ids: set[str] = field(default_factory=set)
    anonymous_compactions: int = 0
    anonymous_compaction_open: bool = False
    usage: TokenUsage | None = None
    settled_input_tokens: int = 0
    settled_cached_input_tokens: int = 0
    settled_output_tokens: int = 0

    def apply(self, payload: RuntimeEventPayload) -> bool:
        if self.overview is not None:
            return False
        match payload:
            case ToolCallStarted(call=call):
                return self._tool_call(call.call_id, call.name, ActivityStatus.RUNNING)
            case ToolCallCompleted(call=call):
                return self._tool_call(
                    call.call_id, call.name, ActivityStatus.COMPLETED
                )
            case ToolCallFailed(call=call):
                return self._tool_call(call.call_id, call.name, ActivityStatus.FAILED)
            case ContextCompactionStarted(compaction_id=compaction_id):
                return self._compaction(compaction_id, ActivityStatus.RUNNING)
            case ContextCompactionCompleted(compaction_id=compaction_id):
                return self._compaction(compaction_id, ActivityStatus.COMPLETED)
            case UsageUpdated(total=total):
                self.usage = total
                return False
            case (
                TurnCompleted(event_name=event_name)
                | TurnFailed(event_name=event_name)
                | TurnCancelled(event_name=event_name)
                | TurnUnknown(event_name=event_name)
            ):
                if "turn" not in event_name.casefold():
                    return False
                error_message = getattr(payload, "error_message", None)
                self.overview = self._build_overview(
                    _TERMINAL_OUTCOMES[type(payload)],
                    error_message,
                )
                self.snapshot = None
                return True
            case TurnStarted():
                self._settle_attempt_usage()
                return False
            case (
                ContentDelta()
                | ToolCallTextDelta()
                | ToolCallPatchUpdated()
                | ToolCallInteraction()
            ):
                return False

    def _tool_call(self, call_id: str, name: str, status: ActivityStatus) -> bool:
        self.tool_call_ids.add(call_id)
        self.snapshot = ActivitySnapshot(
            kind=ActivityKind.TOOL_CALL,
            status=status,
            name=name,
        )
        return True

    def _compaction(self, compaction_id: str | None, status: ActivityStatus) -> bool:
        if compaction_id:
            self.compaction_ids.add(compaction_id)
        elif status is ActivityStatus.RUNNING:
            if not self.anonymous_compaction_open:
                self.anonymous_compactions += 1
                self.anonymous_compaction_open = True
        elif self.anonymous_compaction_open:
            self.anonymous_compaction_open = False
        else:
            self.anonymous_compactions += 1
        self.snapshot = ActivitySnapshot(
            kind=ActivityKind.CONTEXT_COMPACTION,
            status=status,
        )
        return True

    def _build_overview(
        self,
        outcome: ActivityOutcome,
        error_message: str | None,
    ) -> ActivityOverview:
        usage = self.usage
        input_tokens = self.settled_input_tokens
        cached_input_tokens = self.settled_cached_input_tokens
        output_tokens = self.settled_output_tokens
        if usage is not None:
            input_tokens += max(0, usage.input_tokens or 0)
            cached_input_tokens += max(0, usage.cached_input_tokens or 0)
            output_tokens += max(0, usage.output_tokens or 0)
        if error_message is not None and len(error_message) > _MAX_ERROR_MESSAGE:
            error_message = error_message[: _MAX_ERROR_MESSAGE - 1] + "…"
        return ActivityOverview(
            outcome=outcome,
            error_message=error_message,
            tool_calls=len(self.tool_call_ids),
            context_compactions=len(self.compaction_ids) + self.anonymous_compactions,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
        )

    def _settle_attempt_usage(self) -> None:
        usage = self.usage
        if usage is None:
            return
        self.settled_input_tokens += max(0, usage.input_tokens or 0)
        self.settled_cached_input_tokens += max(0, usage.cached_input_tokens or 0)
        self.settled_output_tokens += max(0, usage.output_tokens or 0)
        self.usage = None


def turn_key(item: RuntimeOutputEvent) -> tuple[str, str]:
    return item.envelope.session_id, item.envelope.turn_id


def snapshot_line(translator: Translator, snapshot: ActivitySnapshot) -> ActivityLine:
    return ActivityLine(
        icon=_STATUS_ICONS[snapshot.status],
        label=translator.text(f"activity.kind.{snapshot.kind.value}"),
        name=snapshot.name,
    )


def overview_lines(
    translator: Translator,
    overview: ActivityOverview,
) -> tuple[str, ...]:
    lines: list[str] = []
    if overview.error_message:
        lines.append(
            translator.text("activity.error", {"error": overview.error_message})
        )
    rendered = translator.text(
        "activity.overview",
        {
            "tool_calls": overview.tool_calls,
            "context_compactions": overview.context_compactions,
            "input_tokens": overview.input_tokens,
            "cached_input_tokens": overview.cached_input_tokens,
            "output_tokens": overview.output_tokens,
        },
    )
    lines.extend(rendered.splitlines())
    return tuple(lines)


__all__ = [
    "ActivityKind",
    "ActivityLine",
    "ActivityOutcome",
    "ActivityOverview",
    "ActivityReducer",
    "ActivitySnapshot",
    "ActivityStatus",
    "overview_lines",
    "snapshot_line",
    "turn_key",
]
