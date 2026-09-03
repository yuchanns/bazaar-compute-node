from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import shutil
from collections.abc import AsyncIterable, Awaitable, Callable
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid7

from ..core.models import InboundAttachment

_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


class AttachmentMaterializer:
    def __init__(
        self,
        workspace: Callable[[], Path],
        referenced_paths: Callable[[], Awaitable[set[str]]],
        *,
        max_file_bytes: int = 25 * 1024 * 1024,
        max_workspace_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        self._workspace = workspace
        self._referenced_paths = referenced_paths
        self._max_file_bytes = max_file_bytes
        self._max_workspace_bytes = max_workspace_bytes
        self._lock = asyncio.Lock()

    async def reconcile(self) -> None:
        root = self._workspace() / "attachments"
        staging = root / ".staging"
        await asyncio.to_thread(staging.mkdir, parents=True, exist_ok=True, mode=0o700)
        staged_files = await asyncio.to_thread(
            lambda: tuple(
                path
                for path in staging.iterdir()
                if path.is_file() and not path.is_symlink()
            )
        )
        for path in staged_files:
            await asyncio.to_thread(path.unlink)
        referenced = await self._referenced_paths()
        attachment_directories = await asyncio.to_thread(
            lambda: tuple(
                path
                for path in root.iterdir()
                if path != staging and path.is_dir() and not path.is_symlink()
            )
        )
        for path in attachment_directories:
            try:
                if UUID(path.name).version != 7:
                    continue
            except ValueError:
                continue
            prefix = str(PurePosixPath("attachments", path.name)) + "/"
            if any(relative_path.startswith(prefix) for relative_path in referenced):
                continue
            await asyncio.to_thread(shutil.rmtree, path)

    async def _stage(
        self,
        source: bytes | AsyncIterable[bytes],
        temporary: Path,
        destination: Path,
        root: Path,
    ) -> int:
        """Write an attachment aside, then move it into place once it is whole."""

        current_size = await asyncio.to_thread(self._stored_size, root)
        descriptor: int | None = None
        try:
            descriptor = await asyncio.to_thread(
                os.open,
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                size = await self._write(descriptor, source, current_size)
                await asyncio.to_thread(os.fsync, descriptor)
                stored = await asyncio.to_thread(os.fstat, descriptor)
                if stored.st_size != size:
                    raise OSError(
                        "attachment write size mismatch: "
                        f"expected {size}, got {stored.st_size}"
                    )
            finally:
                await asyncio.to_thread(os.close, descriptor)
                descriptor = None
            await asyncio.to_thread(destination.parent.mkdir, parents=True, mode=0o700)
            await asyncio.to_thread(os.replace, temporary, destination)
            if os.name != "nt":
                await asyncio.to_thread(destination.chmod, 0o600)
            return size
        except BaseException:
            if descriptor is not None:
                await asyncio.to_thread(os.close, descriptor)
            if await asyncio.to_thread(temporary.exists):
                await asyncio.to_thread(temporary.unlink)
            raise

    async def _write(
        self,
        descriptor: int,
        source: bytes | AsyncIterable[bytes],
        current_size: int,
    ) -> int:
        """Write the source through, refusing it the moment it goes over a limit."""

        if isinstance(source, bytes):
            self._check_room(len(source), current_size)
            await asyncio.to_thread(_write_all, descriptor, source)
            return len(source)
        size = 0
        async for chunk in source:
            if not isinstance(chunk, bytes):
                raise TypeError("attachment stream must yield bytes")
            size += len(chunk)
            self._check_room(size, current_size)
            await asyncio.to_thread(_write_all, descriptor, chunk)
        return size

    def _check_room(self, size: int, current_size: int) -> None:
        if size > self._max_file_bytes:
            raise ValueError("attachment exceeds the per-file size limit")
        if current_size + size > self._max_workspace_bytes:
            raise ValueError("attachment workspace quota exceeded")

    async def materialize(
        self,
        source: bytes | AsyncIterable[bytes],
        *,
        name: str,
        kind: str,
        media_type: str | None = None,
    ) -> InboundAttachment:
        if not name or not kind:
            raise ValueError("attachment name and kind must be non-empty")
        attachment_id = str(uuid7())
        suffix = await asyncio.to_thread(_attachment_suffix, name, media_type)
        relative = PurePosixPath(
            "attachments", attachment_id, f"content{suffix.lower()}"
        )
        root = self._workspace() / "attachments"
        staging = root / ".staging"
        destination = self._workspace().joinpath(*relative.parts)
        temporary = staging / f"{attachment_id}.part"
        async with self._lock:
            await asyncio.to_thread(
                staging.mkdir, parents=True, exist_ok=True, mode=0o700
            )
            size = await self._stage(source, temporary, destination, root)
        return InboundAttachment(
            attachment_id=attachment_id,
            name=Path(name).name,
            kind=kind,
            state="ready",
            media_type=media_type,
            relative_path=str(relative),
            size_bytes=size,
        )

    def failed(
        self,
        *,
        name: str,
        kind: str,
        error: str,
        media_type: str | None = None,
    ) -> InboundAttachment:
        return InboundAttachment(
            attachment_id=str(uuid7()),
            name=Path(name).name or "attachment.bin",
            kind=kind,
            state="failed",
            media_type=media_type,
            error=error,
        )

    @staticmethod
    def _stored_size(root: Path) -> int:
        if not root.exists():
            return 0
        return sum(
            path.stat().st_size
            for pattern in ("*/content", "*/content.*")
            for path in root.glob(pattern)
            if path.is_file() and not path.is_symlink()
        )


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written == 0:
            raise OSError("attachment write made no progress")
        view = view[written:]


def _attachment_suffix(name: str, media_type: str | None) -> str:
    suffix = Path(name).suffix
    if _SAFE_SUFFIX.fullmatch(suffix):
        return suffix
    media_type = (media_type or "").partition(";")[0].strip().lower()
    if not media_type or media_type.endswith("/octet-stream"):
        return ""
    guessed = mimetypes.guess_extension(media_type) or ""
    return guessed if _SAFE_SUFFIX.fullmatch(guessed) else ""


__all__ = ["AttachmentMaterializer"]
