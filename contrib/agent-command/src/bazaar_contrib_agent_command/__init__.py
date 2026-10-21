"""Register independent Agent management commands through the public services."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Self

from bazaar_contrib_channel import ChannelBuilderConfig
from bazaar_contrib_config import CONFIGURATION
from bazaar_contrib_harness import HarnessBuilderConfig
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_contrib_sidecar import CLIENT
from bazaar_core import Composition, Context, Contrib, ServiceKey
from pydantic import BaseModel, ConfigDict, PrivateAttr, ValidationInfo, model_validator

from .management import AgentConfiguration, AgentManagement

__all__ = [
    "AGENT_CONFIGURATION",
    "AgentConfiguration",
    "AgentManagement",
    "command_entry",
    "commands",
]


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    _channels: tuple[str, ...] = PrivateAttr(default=())
    _harnesses: tuple[str, ...] = PrivateAttr(default=())

    @model_validator(mode="after")
    def declarations(self, info: ValidationInfo) -> Self:
        if isinstance(info.context, Composition):
            channels: list[str] = []
            harnesses: list[str] = []
            for entry in info.context.entries:
                model = entry.definition.config
                if model is not None and issubclass(model, ChannelBuilderConfig):
                    channels.append(model.kind)
                elif model is not None and issubclass(model, HarnessBuilderConfig):
                    harnesses.append(model.kind)
            self._channels = tuple(channels)
            self._harnesses = tuple(harnesses)
        return self


AGENT_CONFIGURATION = ServiceKey[AgentManagement]("agent.configuration")
command_entry = CommandEntry(
    contrib="agent-command",
    help="Manage agents",
)


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    from .cli import create_commands

    client = ctx.accept(CLIENT)
    timeout = client.timeout
    client.timeout = None
    try:
        service = AgentManagement(
            ctx.accept(CONFIGURATION), client, config._channels, config._harnesses
        )
        ctx.offer(AGENT_CONFIGURATION, service)
        command = await asyncio.to_thread(create_commands, service, client)
        with ctx.accept(COMMANDS).register(command):
            yield
    finally:
        client.timeout = timeout


commands = Contrib(
    config=Config,
    accepts=[CONFIGURATION, COMMANDS, CLIENT],
    offers=[AGENT_CONFIGURATION],
    lifespan=lifespan,
)
