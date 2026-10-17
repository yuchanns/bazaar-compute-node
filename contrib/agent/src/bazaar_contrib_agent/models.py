"""Agent-owned entity configuration and the identity visible to its consumers."""

from __future__ import annotations

from typing import Literal, Self
lazy from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderConfig(BaseModel):
    """Keep each provider's selection and parameters with its owning agent."""

    model_config = ConfigDict(extra="allow", frozen=True)
    kind: str = Field(min_length=1)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1)
    workspace: Path | None = None
    mode: Literal["session", "dangerous_individual"] = "session"
    idle_timeout: float = Field(default=0, ge=0, allow_inf_nan=False)
    channel: tuple[ProviderConfig, ...] = ()
    harness: tuple[ProviderConfig, ...] = ()


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agents: tuple[AgentConfig, ...] = ()

    @model_validator(mode="after")
    def identities(self) -> Self:
        for field in ("id", "name"):
            values = [getattr(agent, field) for agent in self.agents]
            if len(values) != len(set(values)):
                raise ValueError(f"agents.{field} values must be unique")
        return self


class Agent(BaseModel):
    """The identity and persistent workspace of a running agent instance."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    workspace: Path
