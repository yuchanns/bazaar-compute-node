from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import tempfile
from collections.abc import AsyncIterable, Awaitable, Callable, Collection
from pathlib import Path, PurePosixPath
from uuid import UUID

from bazaar_contrib_messages.models import InboundAttachment

_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


class AttachmentMaterializer:
    def __init__(
        self,
        workspace: Callable[[], Path],
        referenced_paths: Callable[[], Awaitable[Collection[str]]],
        *,
        max_file_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self._workspace = workspace
        self._referenced_paths = referenced_paths
        self._max_file_bytes = max_file_bytes

    async def reconcile(self) -> None:
        root = self._workspace() / "attachments"
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True, mode=0o700)
        files = await asyncio.to_thread(
            lambda: tuple(
                path
                for path in root.iterdir()
                if path.is_file() and not path.is_symlink()
            )
        )
        referenced = set(await self._referenced_paths())
        for path in files:
            if not (path.name.startswith(".bcn-") and path.name.endswith(".part")):
                try:
                    UUID(path.stem)
                except ValueError:
                    continue
            if str(PurePosixPath("attachments", path.name)) in referenced:
                continue
            await asyncio.to_thread(path.unlink)

    async def _stage(
        self,
        source: AsyncIterable[bytes],
        destination: Path,
    ) -> int:
        """Write an attachment aside, then move it into place once it is whole."""

        descriptor, temporary = await asyncio.to_thread(
            tempfile.mkstemp,
            dir=destination.parent,
            prefix=".bcn-",
            suffix=".part",
        )
        temporary = Path(temporary)
        try:
            try:
                size = await self._write(descriptor, source)
                await asyncio.to_thread(os.fsync, descriptor)
                stored = await asyncio.to_thread(os.fstat, descriptor)
                if stored.st_size != size:
                    raise OSError(
                        "attachment write size mismatch: "
                        f"expected {size}, got {stored.st_size}"
                    )
            finally:
                await asyncio.to_thread(os.close, descriptor)
            await asyncio.to_thread(os.replace, temporary, destination)
            if os.name != "nt":
                await asyncio.to_thread(destination.chmod, 0o600)
            return size
        except BaseException:
            if await asyncio.to_thread(temporary.exists):
                await asyncio.to_thread(temporary.unlink)
            raise

    async def _write(
        self,
        descriptor: int,
        source: AsyncIterable[bytes],
    ) -> int:
        """Write the source through, refusing it the moment it goes over a limit."""

        size = 0
        async for chunk in source:
            size += len(chunk)
            if size > self._max_file_bytes:
                raise ValueError("attachment exceeds the per-file size limit")
            await asyncio.to_thread(_write_all, descriptor, chunk)
        return size

    async def materialize(
        self,
        source: AsyncIterable[bytes],
        *,
        attachment_id: str,
        name: str,
        kind: str,
        media_type: str | None = None,
    ) -> InboundAttachment:
        if not name or not kind:
            raise ValueError("attachment name and kind must be non-empty")
        suffix = await asyncio.to_thread(_attachment_suffix, name, media_type)
        relative = PurePosixPath("attachments", f"{attachment_id}{suffix.lower()}")
        root = self._workspace() / "attachments"
        destination = self._workspace().joinpath(*relative.parts)
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True, mode=0o700)
        size = await self._stage(source, destination)
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
        attachment_id: str,
        name: str,
        kind: str,
        error: str | None,
        media_type: str | None = None,
    ) -> InboundAttachment:
        return InboundAttachment(
            attachment_id=attachment_id,
            name=Path(name).name or "attachment.bin",
            kind=kind,
            state="failed",
            media_type=media_type,
            error=error,
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
