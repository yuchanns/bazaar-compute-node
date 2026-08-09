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
        for path in await asyncio.to_thread(lambda: tuple(staging.iterdir())):
            if path.is_file() and not path.is_symlink():
                await asyncio.to_thread(path.unlink)
        referenced = await self._referenced_paths()
        for path in await asyncio.to_thread(lambda: tuple(root.iterdir())):
            if path == staging or not path.is_dir() or path.is_symlink():
                continue
            try:
                if UUID(path.name).version != 7:
                    continue
            except ValueError:
                continue
            prefix = str(PurePosixPath("attachments", path.name)) + "/"
            if any(relative_path.startswith(prefix) for relative_path in referenced):
                continue
            await asyncio.to_thread(shutil.rmtree, path)

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
        suffix = Path(name).suffix
        if not _SAFE_SUFFIX.fullmatch(suffix):
            guessed = mimetypes.guess_extension(media_type or "") or ".bin"
            suffix = guessed if _SAFE_SUFFIX.fullmatch(guessed) else ".bin"
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
            current_size = await asyncio.to_thread(self._stored_size, root)
            size = 0
            try:
                with temporary.open("xb") as output:
                    if isinstance(source, bytes):
                        size = len(source)
                        if size > self._max_file_bytes:
                            raise ValueError(
                                "attachment exceeds the per-file size limit"
                            )
                        if current_size + size > self._max_workspace_bytes:
                            raise ValueError("attachment workspace quota exceeded")
                        await asyncio.to_thread(output.write, source)
                    else:
                        async for chunk in source:
                            if not isinstance(chunk, bytes):
                                raise TypeError("attachment stream must yield bytes")
                            size += len(chunk)
                            if size > self._max_file_bytes:
                                raise ValueError(
                                    "attachment exceeds the per-file size limit"
                                )
                            if current_size + size > self._max_workspace_bytes:
                                raise ValueError("attachment workspace quota exceeded")
                            await asyncio.to_thread(output.write, chunk)
                    await asyncio.to_thread(output.flush)
                    await asyncio.to_thread(os.fsync, output.fileno())
                await asyncio.to_thread(
                    destination.parent.mkdir, parents=True, mode=0o700
                )
                await asyncio.to_thread(os.replace, temporary, destination)
                if os.name != "nt":
                    await asyncio.to_thread(destination.chmod, 0o600)
            except BaseException:
                if temporary.exists():
                    await asyncio.to_thread(temporary.unlink)
                raise
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
            for path in root.glob("*/content.*")
            if path.is_file() and not path.is_symlink()
        )


__all__ = ["AttachmentMaterializer"]
