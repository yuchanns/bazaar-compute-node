"""Real TCP configuration and file contribs used by the installed-node workflow."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated
lazy import asyncio
lazy import json
lazy import sys
lazy from typing import Any

from bazaar_contrib_agent import AGENT, AgentConfig, compose
from bazaar_contrib_config import (
    CONFIG_PREPARE,
    CONFIG_STORE,
    ConfigCommitUnknown,
    ConfigConflict,
    ConfigSnapshot,
    ConfigUnavailable,
)
from bazaar_core import Context, Contrib, ContribEntry, ServiceKey
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
lazy import aiofiles

DIRECTORY = ServiceKey[Path]("example.directory")
MISSING = ServiceKey[Path]("example.missing")


class RemoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = Field(ge=1, le=65535)
    key: str = Field(min_length=1)


class RemoteOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: Annotated[
        RemoteConfig,
        BeforeValidator(
            lambda options: dict(option.split("=", 1) for option in options)
        ),
    ]


class RemoteStore:
    def __init__(self, config: RemoteConfig) -> None:
        self.config = config
        self.closed = False

    async def request(self, operation: str, **data: Any):
        if self.closed:
            raise ConfigUnavailable("remote carrier has closed")
        writer = None
        try:
            reader, writer = await asyncio.open_connection(
                self.config.host, self.config.port
            )
            writer.write(
                (
                    json.dumps({"operation": operation, "key": self.config.key, **data})
                    + "\n"
                ).encode()
            )
            await writer.drain()
            response = await reader.readline()
            if not response:
                raise ConnectionError(
                    "configuration connection closed before its response"
                )
            result = json.loads(response)
            if result.get("conflict"):
                raise ConfigConflict("remote configuration revision changed")
            if result.get("missing"):
                return None
            return ConfigSnapshot(result["payload"], result["revision"])
        except (OSError, ConnectionError) as error:
            if operation == "write":
                raise ConfigCommitUnknown("remote write outcome is unknown") from error
            raise ConfigUnavailable("remote configuration cannot be read") from error
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()

    async def read(self) -> ConfigSnapshot | None:
        return await self.request("read")

    async def write(self, payload, *, expected_revision: str | None) -> ConfigSnapshot:
        result = await self.request(
            "write", payload=dict(payload), expected_revision=expected_revision
        )
        assert result is not None
        return result


@asynccontextmanager
async def remote_lifespan(ctx: Context, config: RemoteOptions):
    store = RemoteStore(config.options)
    ctx.offer(CONFIG_STORE, store)
    try:
        yield
    finally:
        store.closed = True


class DirectoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path


@asynccontextmanager
async def directory_lifespan(ctx: Context, config: DirectoryConfig):
    await asyncio.to_thread(config.path.mkdir, parents=True, exist_ok=True)
    ctx.offer(DIRECTORY, config.path)
    yield


@asynccontextmanager
async def journal_lifespan(ctx: Context):
    async with aiofiles.open(
        ctx.accept(DIRECTORY) / f"{ctx.accept(AGENT).name}.txt", "a"
    ) as stream:
        await stream.write("started\n")
        await stream.flush()
        try:
            yield
        finally:
            await stream.write("released\n")


@asynccontextmanager
async def dependent_lifespan(ctx: Context):
    async with aiofiles.open(ctx.accept(MISSING) / "dependent.txt", "a") as stream:
        await stream.write("dependency available\n")
        yield


remote = Contrib(config=RemoteOptions, offers=[CONFIG_STORE], lifespan=remote_lifespan)
directory = Contrib(
    config=DirectoryConfig, offers=[DIRECTORY], lifespan=directory_lifespan
)
journal = Contrib(accepts=[AGENT, DIRECTORY], lifespan=journal_lifespan)
dependent = Contrib(accepts=[MISSING], lifespan=dependent_lifespan)


class AgentsConfig(BaseModel):
    """Agent identities without providers, each journaling into the directory."""

    model_config = ConfigDict(extra="forbid")
    agents: tuple[AgentConfig, ...] = ()


@asynccontextmanager
async def file_agent_lifespan(ctx: Context, config: AgentsConfig):
    async with AsyncExitStack() as stack:
        for item in config.agents:
            composition = compose(item)
            composition = composition.model_copy(
                update={
                    "entries": (
                        *composition.entries,
                        ContribEntry(name="example-journal", definition=journal),
                    )
                }
            )
            await stack.enter_async_context(ctx.enable(composition, local=(AGENT,)))
        yield


file_agent = Contrib(
    config=AgentsConfig, accepts=[DIRECTORY], lifespan=file_agent_lifespan
)


@asynccontextmanager
async def writer_lifespan(ctx: Context, config: DirectoryConfig):
    del ctx
    async with aiofiles.open(config.path, "a") as stream:
        await stream.write("started\n")
        await stream.flush()
        try:
            yield
        finally:
            await stream.write("released\n")


writer = Contrib(config=DirectoryConfig, lifespan=writer_lifespan)


async def prepare(ctx: Context, _: None) -> None:
    process = await asyncio.create_subprocess_exec(
        "uv",
        "pip",
        "check",
        "--python",
        sys.executable,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, errors = await process.communicate()
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
    report = (output + errors).decode()
    if process.returncode:
        raise RuntimeError(report)
    await asyncio.to_thread(
        (ctx.accept(DIRECTORY) / "dependencies.txt").write_text, report
    )


preparation = Contrib(
    config=DirectoryConfig,
    offers=[DIRECTORY],
    lifespan=directory_lifespan,
    listen={CONFIG_PREPARE: prepare},
)
