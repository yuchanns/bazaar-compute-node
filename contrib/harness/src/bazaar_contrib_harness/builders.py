"""Declarations and scoped bindings shared by harness providers and agents."""

# Query budgets are supplied by the caller of the public contract.
# ruff: noqa: ASYNC109

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import cache
from pathlib import Path
from typing import Any, ClassVar

from bazaar_core import Composition, Context, Contrib, ContribEntry, ServiceKey
from pydantic import BaseModel, ConfigDict

from .contracts import Harness
from .models import HarnessInput, HarnessInspection

HARNESS = ServiceKey[Harness]("harness.instance")
HARNESS_INPUT = ServiceKey[HarnessInput]("harness.input")


@cache
def harness_builder_key(kind: str) -> ServiceKey[HarnessBuilder]:
    return ServiceKey[HarnessBuilder](f"harness.builder.{kind}")


class HarnessBuilder(ABC):
    def __init__(self, definition: type[HarnessBuilderConfig]):
        self.definition = definition

    @property
    def kind(self) -> str:
        return self.definition.kind

    @property
    def entity_model(self) -> type[BaseModel]:
        return self.definition.entity_model

    @property
    def entry(self) -> Contrib[Any]:
        return self.definition.entry

    @abstractmethod
    async def inspect(self, workspace: Path, *, timeout: float) -> HarnessInspection:
        """Query the provider without creating an Agent or a Harness session."""

    def compose(self, values: dict[str, Any], input: HarnessInput) -> Composition:
        config = self.entity_model.model_validate(values)
        return Composition(
            entries=(
                ContribEntry(
                    name="harness-input",
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


class HarnessBuilderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: ClassVar[str]
    entity_model: ClassVar[type[BaseModel]]
    entry: ClassVar[Contrib[Any]]


class HarnessBuilders:
    def __init__(self):
        self._builders: dict[str, HarnessBuilder] = {}

    @contextmanager
    def register(self, builder: HarnessBuilder) -> Iterator[HarnessBuilder]:
        if builder.kind in self._builders:
            raise ValueError(f"harness builder {builder.kind!r} is already registered")
        self._builders[builder.kind] = builder
        try:
            yield builder
        finally:
            del self._builders[builder.kind]

    def get(self, kind: str) -> HarnessBuilder:
        return self._builders[kind]

    def all(self) -> tuple[HarnessBuilder, ...]:
        return tuple(self._builders.values())


HARNESS_BUILDERS = ServiceKey[HarnessBuilders]("harness.builders")


@asynccontextmanager
async def builders_lifespan(ctx: Context):
    ctx.offer(HARNESS_BUILDERS, HarnessBuilders())
    yield


@asynccontextmanager
async def input_lifespan(ctx: Context, config: HarnessInput):
    ctx.offer(HARNESS_INPUT, config)
    yield


harness_builders = Contrib(offers=[HARNESS_BUILDERS], lifespan=builders_lifespan)
_input = Contrib(config=HarnessInput, offers=[HARNESS_INPUT], lifespan=input_lifespan)
