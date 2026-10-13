from __future__ import annotations

lazy import asyncio
lazy from contextlib import asynccontextmanager
lazy from pathlib import Path

lazy import aiofiles
lazy from pydantic import BaseModel

from bazaar_core import (
    CONTRIB_CHANGED,
    Blocked,
    Composition,
    Context,
    Contrib,
    ContribChange,
    ContribEntry,
    ContribHub,
    ContribSpec,
    Failed,
    Running,
    ServiceKey,
    load,
)


async def test_loading_keeps_errors_in_status_and_runs_an_independent_file_lifespan(
    tmp_path: Path,
):
    loaded = await asyncio.to_thread(
        load, [ContribSpec(contrib="status"), ContribSpec(contrib="not-installed")]
    )
    changes: list[ContribChange] = []
    changed = asyncio.Event()

    def observe(ctx: Context, value: ContribChange):
        del ctx
        changes.append(value)
        if any(
            isinstance(record.state, (Failed, Blocked)) for record in value.snapshots
        ):
            changed.set()

    class FileConfig(BaseModel):
        path: Path

    @asynccontextmanager
    async def writer(ctx: Context, config: FileConfig):
        del ctx
        async with aiofiles.open(config.path, "w") as stream:
            await stream.write("started\n")
            try:
                yield
            finally:
                await stream.write("released\n")

    first = ServiceKey[Path]("cycle.first")
    second = ServiceKey[Path]("cycle.second")

    @asynccontextmanager
    async def cycle(ctx: Context):
        del ctx
        yield

    path = tmp_path / "independent.txt"
    composition = loaded.model_copy(
        update={
            "entries": (
                *loaded.entries,
                ContribEntry(
                    name="writer",
                    definition=Contrib(config=FileConfig, lifespan=writer),
                    config={"path": path},
                ),
                ContribEntry(
                    name="invalid-file",
                    definition=Contrib(config=FileConfig, lifespan=writer),
                    config={"path": []},
                ),
                ContribEntry(
                    name="first",
                    definition=Contrib(
                        offers=[first], accepts=[second], lifespan=cycle
                    ),
                ),
                ContribEntry(
                    name="second",
                    definition=Contrib(
                        offers=[second], accepts=[first], lifespan=cycle
                    ),
                ),
            )
        }
    )
    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="observer",
                        definition=Contrib(listen={CONTRIB_CHANGED: observe}),
                    )
                ]
            )
        ),
        hub.enable(composition) as owner,
    ):
        await changed.wait()
        assert any(
            record.name == "writer" and isinstance(record.state, Running)
            for record in owner.snapshots
        )
        assert any(
            record.name == "not-installed" and isinstance(record.state, Failed)
            for record in hub.snapshots
        )
        assert any(
            record.name == "invalid-file" and isinstance(record.state, Failed)
            for record in hub.snapshots
        )
        assert any(
            record.name == "first" and isinstance(record.state, Blocked)
            for record in hub.snapshots
        )
        assert any(
            record.name == "second" and isinstance(record.state, Blocked)
            for record in hub.snapshots
        )
        assert any(
            record.name == "not-installed"
            for change in changes
            for record in change.snapshots
        )
    text = await asyncio.to_thread(path.read_text)
    assert "started" in text and "released" in text
