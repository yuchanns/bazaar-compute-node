"""Independent file resources with a real provider and consumer lifespan."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
lazy import asyncio

from bazaar_core import Context, Contrib, ServiceKey
from pydantic import BaseModel, ConfigDict, Field
lazy import aiofiles

__all__ = ["consumer", "file", "reader"]

FILE = ServiceKey[Path]("example.file")
CONSUMER = ServiceKey[Path]("example.consumer")


class Metadata(BaseModel):
    owner: str = ""
    project: str = ""


class Label(BaseModel):
    name: str
    value: str


class FileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path
    label: str = "default"
    delay: float = Field(default=0, ge=0)
    metadata: Metadata = Field(default_factory=Metadata)
    tags: list[str] = Field(default_factory=list)
    labels: list[Label] = Field(default_factory=list)


@asynccontextmanager
async def file_lifespan(ctx: Context, config: FileConfig):
    async with aiofiles.open(config.path, "a") as stream:
        await stream.write(f"starting:{config.label}\n")
        await stream.flush()
        await asyncio.sleep(config.delay)
        await stream.write(f"started:{config.label}\n")
        await stream.write(config.model_dump_json(exclude={"path"}) + "\n")
        await stream.flush()
        ctx.offer(FILE, config.path)
        try:
            yield
        finally:
            await stream.write(f"released:{config.label}\n")


@asynccontextmanager
async def consumer_lifespan(ctx: Context):
    path = ctx.accept(FILE)
    ctx.offer(CONSUMER, path)
    async with (
        aiofiles.open(path.with_suffix(".consumer"), "a") as stream,
        aiofiles.open(path, "a") as journal,
    ):
        await stream.write("consumer started\n")
        await stream.flush()
        await journal.write("consumer started\n")
        await journal.flush()
        try:
            yield
        finally:
            await stream.write("consumer released\n")
            await journal.write("consumer released\n")


@asynccontextmanager
async def reader_lifespan(ctx: Context):
    async with aiofiles.open(ctx.accept(CONSUMER), "a") as stream:
        await stream.write("reader started\n")
        await stream.flush()
        try:
            yield
        finally:
            await stream.write("reader released\n")


file = Contrib(config=FileConfig, offers=[FILE], lifespan=file_lifespan)
consumer = Contrib(accepts=[FILE], offers=[CONSUMER], lifespan=consumer_lifespan)
reader = Contrib(accepts=[CONSUMER], lifespan=reader_lifespan)
