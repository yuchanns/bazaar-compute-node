"""An in-memory carrier for temporary configuration compositions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from uuid import uuid7
lazy from collections.abc import Mapping

from bazaar_core import Contrib
lazy from bazaar_core import Context

from .contracts import CONFIG_STORE, ConfigConflict, ConfigSnapshot


class MemoryConfigStore:
    def __init__(self) -> None:
        self._snapshot: ConfigSnapshot | None = None

    async def read(self) -> ConfigSnapshot | None:
        return deepcopy(self._snapshot)

    async def write(
        self, payload: Mapping[str, object], *, expected_revision: str | None
    ) -> ConfigSnapshot:
        revision = self._snapshot.revision if self._snapshot is not None else None
        if revision != expected_revision:
            raise ConfigConflict("memory configuration changed; read it again")
        self._snapshot = ConfigSnapshot(deepcopy(dict(payload)), uuid7().hex)
        return deepcopy(self._snapshot)


@asynccontextmanager
async def lifespan(ctx: Context):
    ctx.offer(CONFIG_STORE, MemoryConfigStore())
    yield


memory = Contrib(offers=[CONFIG_STORE], lifespan=lifespan)
