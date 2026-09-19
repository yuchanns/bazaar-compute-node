"""The HTTP face of the server."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .access import AccessGate
from .accounts import ensure_admin
from .config import ServerConfiguration
from .consumers import Consumers
from .control import Controls
from .gate import MAX_BODY_BYTES, Gate
from .pages import routes
from .protocol import (
    PROTOCOL_HEADER,
    PROTOCOL_VERSION,
    GetUpdatesRequest,
    ReportEventsRequest,
    error,
    ok,
)
from .registry import load_storage_factory
from .rendering import Renderer, Stale
from .sessions import Sessions, load_session_key
from .storage import Computer, IStorage, StorageContext

_log = logging.getLogger("bazaar_compute_server")


def create_app(configuration: ServerConfiguration, data_dir: Path) -> Starlette:
    storage = load_storage_factory(configuration.storage)(
        StorageContext(
            options=configuration.storage_options,
            data_dir=data_dir,
            retention_days=configuration.retention_days,
        )
    )

    sessions = Sessions()
    controls = Controls()
    consumers = Consumers()
    consumers.on("control.result", controls.result)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await storage.start()
        try:
            sessions.key = await load_session_key(data_dir)
            account, password = await ensure_admin(storage)
            if password is not None:
                # the only time the password exists in the clear; log it
                # where whoever started the server is looking
                _log.warning(
                    "created account %s with password %s; log in and change it",
                    account.name,
                    password,
                )
            yield
        finally:
            await controls.close()
            await storage.stop()

    renderer = Renderer()

    async def closed(request: Request, exc: Exception) -> Response:
        """An error as a page for people and as the envelope for nodes."""

        status = exc.status_code if isinstance(exc, HTTPException) else 500
        if request.url.path.startswith("/node/"):
            return JSONResponse(
                error("internal_error" if status == 500 else "not_found", str(exc)),
                status_code=status,
            )
        return renderer.error(request, status)

    app = Starlette(
        routes=[
            Route("/node/reportEvents", report_events, methods=["POST"]),
            Route("/node/getUpdates", get_updates, methods=["POST"]),
            *routes(storage, sessions, controls),
            Mount(
                "/static",
                StaticFiles(
                    directory=str(
                        files("bazaar_compute_server").joinpath("resources", "static")
                    )
                ),
                name="static",
            ),
        ],
        middleware=[
            Middleware(Stale),
            Middleware(Gate, storage=storage, sessions=sessions),
            Middleware(AccessGate, storage=storage),
        ],
        exception_handlers={HTTPException: closed, Exception: closed},
        lifespan=lifespan,
    )
    app.state.storage = storage
    app.state.controls = controls
    app.state.consumers = consumers
    return app


async def report_events(request: Request) -> Response:
    computer = await _node(request)
    if isinstance(computer, Response):
        return computer
    body = await _read_report(request)
    if isinstance(body, Response):
        return body
    try:
        report = ReportEventsRequest.model_validate(json.loads(body))
    except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as failure:
        return _reply(error("invalid_request", str(failure)), status=400)
    accepted = await _storage(request).record_events(
        computer.id, report.run_id, report.events
    )
    consumers: Consumers = request.app.state.consumers
    consumers.consume(computer, report.events)
    return _reply(ok({"accepted": accepted}))


async def get_updates(request: Request) -> Response:
    computer = await _node(request)
    if isinstance(computer, Response):
        return computer
    body = await _read_report(request)
    if isinstance(body, Response):
        return body
    try:
        poll = GetUpdatesRequest.model_validate(json.loads(body))
    except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as failure:
        return _reply(error("invalid_request", str(failure)), status=400)
    controls: Controls = request.app.state.controls
    # a node that hangs up while its poll is held is not kept waiting for;
    # the next message on the connection after the body is its going away
    waiting = asyncio.create_task(controls.updates(computer.id, after=poll.offset))
    gone = asyncio.ensure_future(request.receive())
    done, _ = await asyncio.wait({waiting, gone}, return_when=asyncio.FIRST_COMPLETED)
    if waiting not in done:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        return _reply(ok({"updates": []}))
    gone.cancel()
    await asyncio.gather(gone, return_exceptions=True)
    return _reply(ok({"updates": waiting.result()}))


async def _node(request: Request) -> Computer | Response:
    """The computer a /node/ call speaks for, once it names a protocol we speak."""

    if request.headers.get(PROTOCOL_HEADER) != str(PROTOCOL_VERSION):
        return _reply(
            error(
                "unsupported_protocol",
                f"this server speaks protocol {PROTOCOL_VERSION}",
            ),
            status=400,
        )
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    computer = (
        await _storage(request).authenticate(token) if scheme == "Bearer" else None
    )
    if computer is None:
        return _reply(
            error("unauthorized", "the token names no known computer"), status=401
        )
    return computer


async def _read_report(request: Request) -> bytes | Response:
    """The body, unless it is more than one report is allowed to be."""

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return _reply(
                error("invalid_request", f"a report is at most {MAX_BODY_BYTES} bytes"),
                status=413,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _storage(request: Request) -> IStorage:
    return request.app.state.storage


def _reply(payload: dict[str, object], *, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


__all__ = ["MAX_BODY_BYTES", "create_app", "get_updates", "report_events"]
