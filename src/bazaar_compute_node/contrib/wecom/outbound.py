from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ...core.models import ChannelTargetKind, OutboundAttachment

CHUNK_SIZE = 512 * 1024
MIN_MEDIA_BYTES = 5
MAX_FILENAME_BYTES = 256

_MEDIA_RULES = {
    ".amr": ("voice", 2 * 1024 * 1024),
    ".gif": ("image", 10 * 1024 * 1024),
    ".jpeg": ("image", 10 * 1024 * 1024),
    ".jpg": ("image", 10 * 1024 * 1024),
    ".mp4": ("video", 10 * 1024 * 1024),
    ".png": ("image", 10 * 1024 * 1024),
}
_FILE_LIMIT = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PreparedAttachment:
    descriptor: OutboundAttachment
    media_type: str
    content: bytes
    md5: str

    @property
    def chunks(self) -> tuple[bytes, ...]:
        return tuple(
            self.content[offset : offset + CHUNK_SIZE]
            for offset in range(0, len(self.content), CHUNK_SIZE)
        )


@dataclass(frozen=True, slots=True)
class UploadResult:
    media_id: str | None
    receipts: tuple[dict[str, object], ...]
    error_kind: str | None = None
    error_message: str | None = None


def media_type_for_filename(filename: str) -> tuple[str, int]:
    suffix = PurePosixPath(filename).suffix.lower()
    return _MEDIA_RULES.get(suffix, ("file", _FILE_LIMIT))


def encode_request(command: str, request_id: str, body: Mapping[str, object]) -> str:
    return json.dumps(
        {
            "cmd": command,
            "headers": {"req_id": request_id},
            "body": body,
        },
        separators=(",", ":"),
    )


def visible_message_body(
    *,
    target_id: str,
    target_kind: ChannelTargetKind,
    message_type: str,
    content: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "chatid": target_id,
        "chat_type": 1 if target_kind is ChannelTargetKind.DM else 2,
        "msgtype": message_type,
        message_type: content,
    }


def prepare_attachments(
    workspace: Path, attachments: tuple[OutboundAttachment, ...]
) -> tuple[PreparedAttachment, ...]:
    root = workspace.resolve(strict=True)
    prepared: list[PreparedAttachment] = []
    for attachment in attachments:
        filename_size = len(attachment.name.encode("utf-8"))
        if filename_size > MAX_FILENAME_BYTES:
            raise ValueError(
                f"WeCom attachment filename exceeds 256 bytes: {attachment.name}"
            )
        media_type, maximum_size = media_type_for_filename(attachment.name)
        if attachment.size_bytes < MIN_MEDIA_BYTES:
            raise ValueError(
                f"WeCom attachment must contain at least 5 bytes: {attachment.name}"
            )
        if attachment.size_bytes > maximum_size:
            raise ValueError(
                f"WeCom {media_type} attachment exceeds its size limit: "
                f"{attachment.name}"
            )
        path = root.joinpath(*PurePosixPath(attachment.relative_path).parts)
        current = root
        for part in PurePosixPath(attachment.relative_path).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    f"WeCom attachment path must not contain symlinks: "
                    f"{attachment.name}"
                )
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError(
                f"WeCom attachment path leaves the workspace: {attachment.name}"
            ) from error
        descriptor = os.open(
            resolved,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            file_stat = os.fstat(descriptor)
            opened_stat = os.stat(resolved, follow_symlinks=False)
            if (
                file_stat.st_dev != opened_stat.st_dev
                or file_stat.st_ino != opened_stat.st_ino
            ):
                raise ValueError(
                    f"WeCom attachment changed while it was opened: {attachment.name}"
                )
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(
                    f"WeCom attachment must be a regular file: {attachment.name}"
                )
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read(maximum_size + 1)
        finally:
            os.close(descriptor)
        if file_stat.st_size > maximum_size or len(content) > maximum_size:
            raise ValueError(
                f"WeCom {media_type} attachment exceeds its size limit: "
                f"{attachment.name}"
            )
        if len(content) != attachment.size_bytes:
            raise ValueError(
                f"attachment content changed before WeCom delivery: {attachment.name}"
            )
        if hashlib.sha256(content).hexdigest() != attachment.sha256:
            raise ValueError(
                f"attachment content changed before WeCom delivery: {attachment.name}"
            )
        prepared.append(
            PreparedAttachment(
                descriptor=attachment,
                media_type=media_type,
                content=content,
                md5=hashlib.md5(content, usedforsecurity=False).hexdigest(),
            )
        )
    return tuple(prepared)


__all__ = [
    "CHUNK_SIZE",
    "PreparedAttachment",
    "UploadResult",
    "encode_request",
    "media_type_for_filename",
    "prepare_attachments",
    "visible_message_body",
]
