"""Provider-neutral execution inputs, output events and approval decisions."""

from __future__ import annotations

from pathlib import Path
from time import time_ns
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class HarnessInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    agent_name: str
    workspace: Path
    mode: Literal["session", "dangerous_individual"] = "session"


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    workspace: Path
    instructions: tuple[str, ...] = ()
    environment: dict[str, str] = Field(default_factory=dict)


class HarnessSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    provider_thread_id: str | None = None


class HarnessAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path
    media_type: str | None = None


class HarnessTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    input_text: str
    attachments: tuple[HarnessAttachment, ...] = ()


class HarnessOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    turn_id: str
    kind: str = Field(min_length=1)
    text: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    provider: str | None = None
    provider_event: str | None = None
    provider_session_id: str | None = None
    provider_turn_id: str | None = None
    occurred_at_ms: int | None = None
    raw: JsonValue = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    created_at_ms: int = Field(default_factory=lambda: time_ns() // 1_000_000)


class ApprovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]
    reason: str | None = None
    decided_at_ms: int = Field(default_factory=lambda: time_ns() // 1_000_000)


class HarnessModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    efforts: tuple[str, ...] = ()
    default_effort: str | None = None
    default: bool = False


class HarnessSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    description: str
    source: str


class HarnessDescription(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    models: tuple[HarnessModel, ...] = ()
    skills: tuple[HarnessSkill, ...] = ()


class SessionCloseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    status: Literal["closed", "failed", "unknown"]
    error: str | None = None
