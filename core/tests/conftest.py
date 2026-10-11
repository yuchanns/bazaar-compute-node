lazy from collections.abc import AsyncIterator
lazy from contextlib import AsyncExitStack

import pytest_asyncio


@pytest_asyncio.fixture
async def tasks() -> AsyncIterator[asyncio.TaskGroup]:
    async with asyncio.TaskGroup() as group:
        yield group


@pytest_asyncio.fixture
async def lifespans(tasks: asyncio.TaskGroup) -> AsyncIterator[AsyncExitStack]:
    del tasks
    async with AsyncExitStack() as stack:
        yield stack


lazy import asyncio
