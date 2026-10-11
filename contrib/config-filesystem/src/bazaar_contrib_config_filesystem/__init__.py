"""A TOML configuration carrier with byte revisions and conditional atomic writes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
lazy import asyncio
lazy import os
lazy import tomllib
lazy from collections.abc import Callable, Mapping
lazy from hashlib import sha256
lazy from typing import Any, Self
lazy from uuid import uuid7

from bazaar_contrib_config import CONFIG_STORE
from bazaar_core import Contrib
from pydantic import BaseModel, ConfigDict
lazy import tomli_w
lazy from bazaar_contrib_config import (
    ConfigCommitUnknown,
    ConfigConflict,
    ConfigFormatError,
    ConfigSnapshot,
    ConfigUnavailable,
)
lazy from bazaar_core import Context
lazy from filelock import FileLock

__all__ = ["FileConfig", "FileConfigStore", "filesystem"]


class FileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: tuple[Path, ...] = ()


class FileConfigStore:
    """Own in-flight filesystem operations for the carrier's entire lifespan."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    async def __aenter__(self) -> Self:
        self.path = await asyncio.to_thread(lambda: self.path.expanduser().resolve())
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _call[T](self, operation: Callable[[], T]) -> T:
        if self._closed:
            raise ConfigUnavailable(f"{self.path}: carrier has closed")
        task = asyncio.create_task(asyncio.to_thread(operation))
        self._tasks.add(task)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as error:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001 - Preserve cancellation after the worker has finished.
                    break
            if task.done() and not task.cancelled() and task.exception() is not None:
                error.add_note(f"filesystem operation ended with {task.exception()}")
            raise
        finally:
            self._tasks.discard(task)

    def _read(self) -> ConfigSnapshot | None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ConfigUnavailable(f"cannot read {self.path}: {error}") from error
        try:
            payload = tomllib.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as error:
            raise ConfigFormatError(f"cannot decode {self.path}: {error}") from error
        return ConfigSnapshot(payload, sha256(raw).hexdigest())

    async def read(self) -> ConfigSnapshot | None:
        return await self._call(self._read)

    def _write(
        self, payload: Mapping[str, object], expected_revision: str | None
    ) -> ConfigSnapshot:
        try:
            content = tomli_w.dumps(payload).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ConfigFormatError(f"cannot serialize {self.path}: {error}") from error
        temporary = self.path.with_name(f".{self.path.name}.{uuid7().hex}.tmp")
        committed = False
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with FileLock(str(self.path) + ".lock", mode=0o600):
                try:
                    raw = self.path.read_bytes()
                except FileNotFoundError:
                    raw = None
                revision = sha256(raw).hexdigest() if raw is not None else None
                if revision != expected_revision:
                    raise ConfigConflict(
                        f"{self.path}: source changed; read and validate it again"
                    )
                descriptor = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                committed = True
                if os.name != "nt":
                    descriptor = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
        except OSError as error:
            if committed:
                raise ConfigCommitUnknown(
                    f"{self.path}: replacement completed but durability could not be confirmed"
                ) from error
            raise ConfigUnavailable(f"cannot write {self.path}: {error}") from error
        finally:
            temporary.unlink(missing_ok=True)
        return ConfigSnapshot(
            tomllib.loads(content.decode("utf-8")), sha256(content).hexdigest()
        )

    async def write(
        self, payload: Mapping[str, object], *, expected_revision: str | None
    ) -> ConfigSnapshot:
        return await self._call(lambda: self._write(payload, expected_revision))


@asynccontextmanager
async def lifespan(ctx: Context, config: FileConfig):
    path = (
        config.options[-1]
        if config.options
        else Path(os.environ.get("BCN_HOME") or Path.home()) / ".bcn" / "config.toml"
    )
    async with FileConfigStore(path) as store:
        ctx.offer(CONFIG_STORE, store)
        yield


filesystem = Contrib(config=FileConfig, offers=[CONFIG_STORE], lifespan=lifespan)
