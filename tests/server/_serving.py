from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from starlette.applications import Starlette

from bazaar_compute_server.app import create_app
from bazaar_compute_server.config import ServerConfiguration
from bazaar_compute_server.storage import IStorage


@asynccontextmanager
async def serving(data_dir: Path) -> AsyncIterator[tuple[str, IStorage]]:
    """A real server on a free loopback port, the way `bcs run` starts one."""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
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
