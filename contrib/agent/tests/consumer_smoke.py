"""Consume installed agent instances through local public compositions."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import aiofiles
from aiofiles.threadpool.text import AsyncTextIOWrapper
from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ContribSpec,
    Running,
    ServiceKey,
    load,
)
from bcn_agent_journal import JOURNAL
from blockbuster import blockbuster_ctx

from bazaar_contrib_agent import AGENT, Agent, AgentConfig, compose

REPORT = ServiceKey[AsyncTextIOWrapper]("example.shared-report")


async def exercise(path: Path):
    default = path / ".bcn" / "workspaces" / "Cloud"
    project = path / "project"
    await asyncio.to_thread(project.mkdir)
    await asyncio.to_thread((project / "notes.txt").write_text, "persistent project")
    config = {
        "agents": [
            {"id": "Cloud", "name": "Cloud"},
            {"id": "Tifa", "name": "Tifa", "workspace": str(project)},
        ]
    }
    # Run the installed entry itself before composing an independent consumer.
    for _ in range(2):
        composition = await asyncio.to_thread(
            load, [ContribSpec(contrib="agent", config=config)]
        )
        async with ContribHub() as hub, hub.enable(composition):
            for name in ("Cloud", "Tifa"):
                assert any(
                    record.name == name and isinstance(record.state, Running)
                    for record in hub.snapshots
                )
            assert await asyncio.to_thread(default.is_dir)
        assert "persistent project" in await asyncio.to_thread(
            (project / "notes.txt").read_text
        )

    composition = await asyncio.to_thread(load, [ContribSpec(contrib="agent")])
    async with ContribHub() as hub, hub.enable(composition):
        assert all(record.name == "agent" for record in hub.snapshots)

    journals: dict[str, AsyncTextIOWrapper] = {}
    instances: dict[str, Agent] = {}

    @asynccontextmanager
    async def shared(ctx: Context):
        async with aiofiles.open(path / "shared.txt", "a") as stream:
            ctx.offer(REPORT, stream)
            yield
            await stream.write("shared released\n")

    @asynccontextmanager
    async def observe(ctx: Context):
        agent = ctx.accept(AGENT)
        instances[agent.name] = agent
        journals[agent.name] = ctx.accept(JOURNAL)
        await journals[agent.name].write(f"identity {agent.id}\n")
        await ctx.accept(REPORT).write(f"observed {agent.name}\n")
        yield

    @asynccontextmanager
    async def control(ctx: Context):
        async with AsyncExitStack() as first, AsyncExitStack() as second:
            for stack, item in zip((first, second), config["agents"]):
                composition = compose(AgentConfig.model_validate(item))
                journal = await asyncio.to_thread(
                    load, [ContribSpec(contrib="agent-journal")]
                )
                composition = composition.model_copy(
                    update={
                        "entries": (
                            *composition.entries,
                            *journal.entries,
                            ContribEntry(
                                name="observer",
                                definition=Contrib(
                                    accepts=[AGENT, JOURNAL, REPORT], lifespan=observe
                                ),
                            ),
                        )
                    }
                )
                await stack.enter_async_context(
                    ctx.enable(composition, local=(AGENT, JOURNAL))
                )
            await first.aclose()
            assert journals["Cloud"].closed
            await journals["Tifa"].write("continues after Cloud closes\n")
            await ctx.accept(REPORT).write("shared continues\n")
            yield

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="shared",
                        definition=Contrib(offers=[REPORT], lifespan=shared),
                    ),
                    ContribEntry(
                        name="controller",
                        definition=Contrib(accepts=[REPORT], lifespan=control),
                    ),
                ]
            )
        ),
    ):
        assert await asyncio.to_thread(instances["Cloud"].workspace.samefile, default)
        assert await asyncio.to_thread(instances["Tifa"].workspace.samefile, project)
    for name, workspace in (("Cloud", default), ("Tifa", project)):
        content = await asyncio.to_thread((workspace / "journal.txt").read_text)
        assert f"identity {name}" in content
        assert f"released {name}" in content
        assert journals[name].closed
        if os.name != "nt":
            assert (await asyncio.to_thread(workspace.stat)).st_mode & 0o777 == 0o700
    assert "continues after Cloud closes" in await asyncio.to_thread(
        (project / "journal.txt").read_text
    )
    report = await asyncio.to_thread((path / "shared.txt").read_text)
    assert "observed Cloud" in report and "observed Tifa" in report
    assert "shared continues" in report and "shared released" in report


if __name__ == "__main__":
    with TemporaryDirectory(prefix="bcn-agent-") as directory:
        directory = Path(directory)
        if os.name != "nt":
            (directory / "home").mkdir()
            (directory / "alias").symlink_to(
                directory / "home", target_is_directory=True
            )
            directory = directory / "alias"
        os.environ["BCN_HOME"] = str(directory)
        with blockbuster_ctx():
            asyncio.run(exercise(directory))
