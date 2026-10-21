"""Agent-owned entity configuration and the identity visible to its consumers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID
lazy from pathlib import Path

from bazaar_contrib_channel import ChannelBuilder, ChannelBuilderConfig
from bazaar_contrib_harness import HarnessBuilder, HarnessBuilderConfig, SandboxPolicy
from bazaar_core import Composition
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationInfo,
    model_validator,
)

if TYPE_CHECKING:
    from .loop import AgentLoop


class ProviderConfig(BaseModel):
    """Keep each provider's selection and parameters with its owning agent."""

    model_config = ConfigDict(extra="allow", frozen=True)
    kind: str = Field(min_length=1)


class HarnessConfig(ProviderConfig):
    """Select execution policy independently of the provider's own parameters."""

    sandbox_policy: SandboxPolicy = SandboxPolicy.WORKSPACE_WRITE
    network_access: bool = True


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1)
    workspace: Path | None = None
    mode: Literal["session", "dangerous_individual"] = "session"
    idle_timeout: float = Field(default=0, ge=0, allow_inf_nan=False)
    channel: tuple[ProviderConfig, ...] = ()
    harness: tuple[HarnessConfig, ...] = ()

    def validate_providers(
        self,
        channels: Mapping[str, ChannelBuilder],
        harnesses: Mapping[str, HarnessBuilder],
    ) -> None:
        for providers, builders in (
            (self.channel, channels),
            (self.harness, harnesses),
        ):
            for provider in providers:
                if provider.kind not in builders:
                    raise ValueError(
                        f"provider builder {provider.kind!r} is not enabled"
                    )
                builders[provider.kind].entity_model.model_validate(
                    provider.model_dump(mode="python", exclude={"kind"})
                )


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

    @model_validator(mode="after")
    def providers(self, info: ValidationInfo) -> Self:
        if isinstance(info.context, Composition):
            channels: dict[str, ChannelBuilder] = {}
            harnesses: dict[str, HarnessBuilder] = {}
            for entry in info.context.entries:
                model = entry.definition.config
                if model is not None and issubclass(model, ChannelBuilderConfig):
                    channels[model.kind] = model.builder()
                elif model is not None and issubclass(model, HarnessBuilderConfig):
                    harnesses[model.kind] = model.builder()
            from . import compose

            for agent in self.agents:
                compose(agent, channel_builders=channels, harness_builders=harnesses)
        return self


class Agent(BaseModel):
    """The identity and persistent workspace of a running agent instance."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    workspace: Path
    _loop: AgentLoop | None = PrivateAttr(default=None)
    _command: Path | None = PrivateAttr(default=None)
    _instance_id: UUID | None = PrivateAttr(default=None)
