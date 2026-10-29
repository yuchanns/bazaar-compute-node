"""Standard logging with owned handlers, queued writes, and file rotation."""

from __future__ import annotations

import logging as stdlib
import threading
from contextlib import asynccontextmanager
from enum import Enum, auto
from logging.handlers import QueueHandler
from pathlib import Path
from typing import Literal
lazy import asyncio
lazy import json
lazy import re
lazy from collections.abc import AsyncIterator, Callable
lazy from copy import copy
lazy from datetime import date, datetime, timedelta
lazy from logging.handlers import QueueListener
lazy from queue import Queue
lazy from uuid import uuid7

from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent, AuditFailure
from bazaar_core import Contrib
from concurrent_log_handler import ConcurrentTimedRotatingFileHandler
from pydantic import BaseModel, ByteSize, ConfigDict, Field
lazy from bazaar_core import CURRENT_CONTEXT, Context

__all__ = ["LoggingConfig", "logging"]


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal[
        "debug", "info", "warning", "error", "DEBUG", "INFO", "WARNING", "ERROR"
    ] = "info"
    format: Literal["text", "json"] | None = None
    path: Path | None = None
    paths: dict[Literal["debug", "info", "warning", "error"], Path] = Field(
        default_factory=dict
    )
    retention_days: int = Field(default=7, ge=1)
    max_size: ByteSize = Field(default=ByteSize(20 * 1024 * 1024), gt=0)


_lock = threading.RLock()


class _CaptureState(Enum):
    ACCEPTING = auto()
    CLOSED = auto()


class _QueueHandler(QueueHandler):
    def __init__(self, queue: Queue[stdlib.LogRecord | None]) -> None:
        super().__init__(queue)
        self._state = _CaptureState.ACCEPTING
        self.original_level = 0

    def prepare(self, record: stdlib.LogRecord) -> stdlib.LogRecord:
        record = copy(record)
        record.msg = record.getMessage()
        record.args = None
        if record.exc_info is not None:
            record.exc_text = stdlib.Formatter().formatException(record.exc_info)
            record.exc_info = None
        ctx = CURRENT_CONTEXT.get()
        record.__dict__["contrib"] = ctx.name if ctx is not None else None
        record.__dict__["id"] = str(ctx.id) if ctx is not None else None
        record.__dict__["publisher"] = record.__dict__["contrib"] or record.name
        record.levelname = {"WARNING": "WARN", "CRITICAL": "CRIT"}.get(
            record.levelname, record.levelname
        )
        return record

    def emit(self, record: stdlib.LogRecord) -> None:
        if self._state is _CaptureState.ACCEPTING:
            super().emit(record)


class _Dispatch(stdlib.Handler):
    def __init__(self, handlers: dict[str, stdlib.Handler]) -> None:
        super().__init__()
        self.handlers = handlers

    def emit(self, record: stdlib.LogRecord) -> None:
        level = (
            "error"
            if record.levelno >= stdlib.ERROR
            else "warning"
            if record.levelno >= stdlib.WARNING
            else "info"
            if record.levelno >= stdlib.INFO
            else "debug"
        )
        try:
            self.handlers[level].handle(record)
        except Exception:  # noqa: BLE001 - Keep the listener alive after an output failure.
            self.handleError(record)


class _Formatter(stdlib.Formatter):
    def __init__(self, format: Literal["text", "json"]) -> None:
        super().__init__(
            "[%(asctime)s][%(levelname)-5s][%(publisher)s]"
            "[%(filename)s:%(lineno)d] %(message)s"
        )
        self.output = format

    def formatTime(self, record: stdlib.LogRecord, datefmt: str | None = None) -> str:
        return (
            datetime.fromtimestamp(record.created)
            .astimezone()
            .isoformat(timespec="milliseconds")
        )

    def format(self, record: stdlib.LogRecord) -> str:
        if self.output == "json":
            event = {
                "message": record.getMessage(),
                "logger": record.name,
                "level": stdlib.getLevelName(record.levelno).lower(),
                "created_at_ms": int(record.created * 1000),
                "source": {
                    "contrib": record.__dict__["contrib"],
                    "id": record.__dict__["id"],
                    "module": record.module,
                    "pathname": record.pathname,
                    "lineno": record.lineno,
                    "function": record.funcName,
                },
            }
            event["timestamp"] = self.formatTime(record)
            if record.exc_text:
                event["exception"] = record.exc_text
            if record.stack_info:
                event["stack"] = record.stack_info
            return json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        return super().format(record)


class _DailyFileHandler(ConcurrentTimedRotatingFileHandler):
    def __init__(self, path: Path, retention_days: int, max_size: int) -> None:
        self.retention_days = retention_days
        super().__init__(
            path,
            when="midnight",
            backupCount=retention_days,
            maxBytes=max_size,
            encoding="utf-8",
        )
        for file in self.getFilesToDelete():
            Path(file).unlink()

    def getFilesToDelete(self) -> list[str]:
        path = Path(self.baseFilename)
        prefix = path.name + "."
        oldest = (date.today() - timedelta(days=self.retention_days - 1)).isoformat()
        return [
            str(file)
            for file in path.parent.iterdir()
            if file.is_file()
            and file.name.startswith(prefix)
            and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}(?:\.\d+)?", suffix := file.name[len(prefix) :]
            )
            and suffix[:10] < oldest
        ]


async def _finish(operation: Callable[[], object]) -> None:
    # Wait for thread-owned I/O before its handler can be released.
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


@asynccontextmanager
async def _logging(ctx: Context, config: LoggingConfig) -> AsyncIterator[None]:
    pool: dict[Path | None, stdlib.Handler] = {}
    handlers: dict[str, stdlib.Handler] = {}
    listener: QueueListener | None = None
    capture: _QueueHandler | None = None
    dispatch: _Dispatch | None = None
    root = stdlib.getLogger()
    try:
        queue: Queue[stdlib.LogRecord | None] = Queue()
        capture = _QueueHandler(queue)
        capture.name = f"bazaar_contrib_logging.input.{uuid7()}"
        capture.setLevel(config.level.upper())
        with _lock:
            existing = [h for h in root.handlers if isinstance(h, _QueueHandler)]
            capture.original_level = (
                existing[0].original_level if existing else root.level
            )
            root.addHandler(capture)
            root.setLevel(
                min(capture.original_level, capture.level, *(h.level for h in existing))
            )
        for level in ("debug", "info", "warning", "error"):
            path = config.paths.get(level, config.path)
            handler = pool.get(path)
            if handler is None:
                if path is None:
                    handler = stdlib.StreamHandler()
                else:
                    await asyncio.to_thread(
                        path.parent.mkdir, parents=True, exist_ok=True
                    )
                    handler = await asyncio.to_thread(
                        _DailyFileHandler, path, config.retention_days, config.max_size
                    )
                pool[path] = handler
                handler.name = f"bazaar_contrib_logging.{uuid7()}"
                handler.setLevel(config.level.upper())
                handler.setFormatter(
                    _Formatter(config.format or ("text" if path is None else "json"))
                )
            handlers[level] = handler
        dispatch = _Dispatch(handlers)
        dispatch.setLevel(capture.level)
        listener = QueueListener(queue, dispatch, respect_handler_level=True)
        capture.listener = listener
        listener.start()
        yield
    finally:
        if capture is not None:
            with _lock:
                if capture in root.handlers:
                    root.removeHandler(capture)
                    existing = [
                        h for h in root.handlers if isinstance(h, _QueueHandler)
                    ]
                    root.setLevel(
                        min((capture.original_level, *(h.level for h in existing)))
                    )
            capture.acquire()
            try:
                capture._state = _CaptureState.CLOSED
            finally:
                capture.release()
        try:
            if listener is not None:
                await _finish(listener.stop)
        finally:
            if capture is not None:
                capture.close()
            if dispatch is not None:
                dispatch.close()
            for handler in pool.values():
                await _finish(handler.close)


async def _audit(ctx: Context, event: AuditEvent) -> None:
    stdlib.getLogger("bazaar_compute_node.audit").log(
        stdlib.ERROR if isinstance(event, AuditFailure) else stdlib.INFO,
        "%s",
        json.dumps(
            event.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        ),
    )


logging = Contrib(config=LoggingConfig, lifespan=_logging, listen={AUDIT_EVENT: _audit})
