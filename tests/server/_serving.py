from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
import uvicorn
from starlette.applications import Starlette

from bazaar_compute_server.app import create_app
from bazaar_compute_server.config import ServerConfiguration
from bazaar_compute_server.storage import IStorage


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@asynccontextmanager
async def serving(
    data_dir: Path, port: int | None = None
) -> AsyncIterator[tuple[str, IStorage]]:
    """A real server on a loopback port, the way `bcs run` starts one."""

    port = port or free_port()
    app: Starlette = create_app(
        ServerConfiguration(listen=f"127.0.0.1:{port}"), data_dir
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}", app.state.storage
    finally:
        server.should_exit = True
        await task


# root, given a password the tests know
TESTER = ("admin", "a password for tests")


async def with_password(storage: IStorage, name: str, password: str) -> None:
    """Give the account a password the tests know."""

    account = await storage.find_account(name)
    assert account is not None, name
    await storage.change_password(account.id, password)


@asynccontextmanager
async def signed_in(
    base: str, storage: IStorage
) -> AsyncIterator[aiohttp.ClientSession]:
    """A browser session that has logged in as root."""

    name, password = TESTER
    await with_password(storage, name, password)
    # the jar must be told to keep cookies for a bare IP host
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True)
    ) as session:
        async with session.post(
            f"{base}/login", data={"name": name, "password": password}
        ) as response:
            assert response.status == 204, await response.text()
        yield session
