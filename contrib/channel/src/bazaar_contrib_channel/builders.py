"""Declarations and scoped bindings shared by channel providers and agents."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import cache
from typing import Any, ClassVar

from bazaar_core import Composition, Context, Contrib, ContribEntry, ServiceKey
from pydantic import BaseModel, ConfigDict

from .contracts import IChannel
from .models import ChannelInput

CHANNEL = ServiceKey[IChannel]("channel.instance")
CHANNEL_INPUT = ServiceKey[ChannelInput]("channel.input")


@cache
def channel_builder_key(kind: str) -> ServiceKey[ChannelBuilder]:
    return ServiceKey[ChannelBuilder](f"channel.builder.{kind}")


class ChannelBuilder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    definition: type[ChannelBuilderConfig]

    @property
    def kind(self) -> str:
        return self.definition.kind

    @property
    def entity_model(self) -> type[BaseModel]:
        return self.definition.entity_model

    @property
    def entry(self) -> Contrib[Any]:
        return self.definition.entry

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.definition.capabilities

    def compose(self, values: dict[str, Any], input: ChannelInput) -> Composition:
        config = self.entity_model.model_validate(values)
        return Composition(
            entries=(
                ContribEntry(
                    name="channel-input",
                    definition=_input,
                    config=input.model_dump(mode="python"),
                ),
                ContribEntry(
                    name=self.kind,
                    definition=self.entry,
                    config=config.model_dump(mode="python"),
                ),
            )
        )


class ChannelBuilderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: ClassVar[str]
    entity_model: ClassVar[type[BaseModel]]
    entry: ClassVar[Contrib[Any]]
    capabilities: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def builder(cls) -> ChannelBuilder:
        return ChannelBuilder(definition=cls)


class ChannelBuilders:
    def __init__(self):
        self._builders: dict[str, ChannelBuilder] = {}

    @contextmanager
    def register(self, builder: ChannelBuilder) -> Iterator[ChannelBuilder]:
        if builder.kind in self._builders:
            raise ValueError(f"channel builder {builder.kind!r} is already registered")
        self._builders[builder.kind] = builder
        try:
            yield builder
        finally:
            del self._builders[builder.kind]

    def get(self, kind: str) -> ChannelBuilder:
        return self._builders[kind]

    def all(self) -> tuple[ChannelBuilder, ...]:
        return tuple(self._builders.values())


CHANNEL_BUILDERS = ServiceKey[ChannelBuilders]("channel.builders")


@asynccontextmanager
async def builders_lifespan(ctx: Context):
    ctx.offer(CHANNEL_BUILDERS, ChannelBuilders())
    yield


@asynccontextmanager
async def input_lifespan(ctx: Context, config: ChannelInput):
    ctx.offer(CHANNEL_INPUT, config)
    yield


channel_builders = Contrib(offers=[CHANNEL_BUILDERS], lifespan=builders_lifespan)
_input = Contrib(config=ChannelInput, offers=[CHANNEL_INPUT], lifespan=input_lifespan)
