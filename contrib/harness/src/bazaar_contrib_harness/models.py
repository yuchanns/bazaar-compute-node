"""Provider-neutral execution inputs, output events and approval decisions."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from time import time_ns
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class SandboxPolicy(StrEnum):
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class HarnessInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    agent_name: str
    workspace: Path
    startup_timeout: float = Field(gt=0, allow_inf_nan=False)
    shutdown_timeout: float = Field(gt=0, allow_inf_nan=False)


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    workspace: Path
    instructions: tuple[str, ...] = ()
    environment: dict[str, str] = Field(default_factory=dict)
    sandbox_policy: SandboxPolicy = SandboxPolicy.WORKSPACE_WRITE


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


class ApprovalScope(StrEnum):
    ONCE = "once"
    SESSION = "session"
    ALWAYS = "always"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    allowed_scopes: tuple[ApprovalScope, ...] = Field(
        default=(ApprovalScope.ONCE,), min_length=1
    )
    created_at_ms: int = Field(default_factory=lambda: time_ns() // 1_000_000)


class ApprovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]
    scope: ApprovalScope = ApprovalScope.ONCE
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


class HarnessMcpTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    description: str | None = None
    input_schema: dict[str, JsonValue]


class HarnessMcpServer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    auth_status: str | None = None
    tools: tuple[HarnessMcpTool, ...] = ()


class HarnessInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    available: bool
    version: str | None = None
    models: tuple[HarnessModel, ...] = ()
    skills: tuple[HarnessSkill, ...] = ()
    mcp_servers: tuple[HarnessMcpServer, ...] = ()
    error: str | None = None


class HarnessExpire(BaseModel):
    """Mark the provider's session contexts for replacement when safe."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
