"""The HTTP face of the server."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .config import ServerConfiguration
from .protocol import (
    PROTOCOL_HEADER,
    PROTOCOL_VERSION,
    ReportEventsRequest,
    error,
    ok,
)
from .registry import load_storage_factory
from .storage import Computer, IStorage, StorageContext

MAX_REPORT_BYTES = 1024 * 1024


def create_app(configuration: ServerConfiguration, data_dir: Path) -> Starlette:
    storage = load_storage_factory(configuration.storage)(
        StorageContext(
            options=configuration.storage_options,
            data_dir=data_dir,
            retention_days=configuration.retention_days,
        )
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await storage.start()
        try:
            yield
        finally:
            await storage.stop()

    app = Starlette(
        routes=[Route("/node/reportEvents", report_events, methods=["POST"])],
        lifespan=lifespan,
    )
    app.state.storage = storage
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
    return _reply(ok({"accepted": accepted}))


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
        if size > MAX_REPORT_BYTES:
            return _reply(
                error(
                    "invalid_request", f"a report is at most {MAX_REPORT_BYTES} bytes"
                ),
                status=413,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _storage(request: Request) -> IStorage:
    return request.app.state.storage


def _reply(payload: dict[str, object], *, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


__all__ = ["MAX_REPORT_BYTES", "create_app", "report_events"]
