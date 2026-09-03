from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

type JsonValue = (
    str | int | float | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
)


class ContentDeltaKind(StrEnum):
    AGENT_MESSAGE = "agent_message"
    PLAN = "plan"
    REASONING_TEXT = "reasoning_text"
    REASONING_SUMMARY = "reasoning_summary"


class ToolCallDeltaKind(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    PROGRESS = "progress"


@dataclass(frozen=True, slots=True)
class RuntimeEventEnvelope:
    session_id: str
    runtime_session_id: str
    turn_id: str
    provider_turn_id: str | None
    occurred_at_ms: int


@dataclass(frozen=True, slots=True)
class FileChangeEntry:
    path: str
    kind: str
    patch: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    parent_call_id: str | None = None
    input: JsonValue = None
    output: JsonValue = None


@dataclass(frozen=True, slots=True)
class ToolCallTextDelta:
    call_id: str
    kind: ToolCallDeltaKind
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallPatchUpdated:
    call_id: str
    changes: tuple[FileChangeEntry, ...]


@dataclass(frozen=True, slots=True)
class ToolCallInteraction:
    call_id: str
    stdin: str
    process_id: str | None = None


@dataclass(frozen=True, slots=True)
class TurnStarted:
    event_name: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    event_name: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnFailed:
    event_name: str
    error_kind: str
    error_message: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    event_name: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnUnknown:
    event_name: str
    error_kind: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContentDelta:
    kind: ContentDeltaKind
    text: str
    index: int | None = None


@dataclass(frozen=True, slots=True)
class ContextCompactionStarted:
    compaction_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContextCompactionCompleted:
    compaction_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolCallCompleted:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolCallFailed:
    call: ToolCall
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class UsageUpdated:
    total: TokenUsage
    last: TokenUsage | None = None
    model_context_window: int | None = None
    cost_usd: float | None = None


type RuntimeEventPayload = (
    TurnStarted
    | TurnCompleted
    | TurnFailed
    | TurnCancelled
    | TurnUnknown
    | ContentDelta
    | ContextCompactionStarted
    | ContextCompactionCompleted
    | ToolCallStarted
    | ToolCallCompleted
    | ToolCallFailed
    | ToolCallTextDelta
    | ToolCallPatchUpdated
    | ToolCallInteraction
    | UsageUpdated
)


@dataclass(frozen=True, slots=True)
class RuntimeOutputEvent:
    envelope: RuntimeEventEnvelope
    payload: RuntimeEventPayload
