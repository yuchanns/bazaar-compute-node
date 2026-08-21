from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from ...core.channel import ChannelDeliveryReceipt, ChannelSendRequest
from ...core.models import OutboundAttachment
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from .api import LarkApi, LarkApiError, LarkTransportError
from .identity import LarkBotIdentity, LarkThreadIdentity, parse_provider_thread_id

MAX_MARKDOWN_CODEPOINTS = 3_500
MAX_FILENAME_BYTES = 256
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 30 * 1024 * 1024
CHUNK_SIZE = 512 * 1024

_IMAGE_SUFFIXES = frozenset(
    {
        ".bmp",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }
)
_AUDIO_SUFFIXES = frozenset({".opus"})
_VIDEO_SUFFIXES = frozenset({".mp4"})
_DOCUMENT_FILE_TYPES = {
    ".doc": "doc",
    ".docx": "doc",
    ".pdf": "pdf",
    ".ppt": "ppt",
    ".pptx": "ppt",
    ".xls": "xls",
    ".xlsx": "xls",
}


@dataclass(frozen=True, slots=True)
class PreparedAttachment:
    descriptor: OutboundAttachment
    workspace: Path
    media_type: str
    message_type: str
    file_type: str
    size_bytes: int
    sha256: str
    device: int
    inode: int

    def open(self) -> BinaryIO:
        descriptor, file_stat = _open_regular_file(self.workspace, self.descriptor)
        if (
            file_stat.st_dev != self.device
            or file_stat.st_ino != self.inode
            or file_stat.st_size != self.size_bytes
        ):
            os.close(descriptor)
            raise ValueError(
                f"Lark attachment changed after preflight: {self.descriptor.name}"
            )
        return os.fdopen(descriptor, "rb")


def split_markdown(
    content: str,
    *,
    limit: int = MAX_MARKDOWN_CODEPOINTS,
) -> tuple[str, ...]:
    """Split markdown by Unicode code points while preserving fenced blocks."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not content:
        return (content,)

    chunks: list[str] = []
    cursor = 0
    fence: _Fence | None = None
    while cursor < len(content):
        prefix = f"{fence.opening}\n" if fence is not None else ""
        end = cursor
        size = len(prefix)
        while end < len(content) and size + 1 <= limit:
            size += 1
            end += 1
        if end == cursor:
            raise ValueError("markdown fence overhead exceeds the provider limit")

        while True:
            next_fence = _advance_fence(
                fence,
                content[cursor:end],
                initial_line_boundary=(
                    fence is not None or cursor == 0 or content[cursor - 1] in "\r\n"
                ),
                terminal_line_complete=(end == len(content) or content[end] in "\r\n"),
            )
            suffix = _closing_suffix(prefix + content[cursor:end], next_fence)
            if len(prefix) + (end - cursor) + len(suffix) <= limit:
                break
            end -= 1
            if end == cursor:
                raise ValueError("markdown fence closure exceeds the provider limit")

        if end < len(content):
            minimum = cursor + max(1, (end - cursor) // 2)
            preferred = _preferred_boundary(content, minimum, end)
            if preferred is not None:
                preferred_fence = _advance_fence(
                    fence,
                    content[cursor:preferred],
                    initial_line_boundary=(
                        fence is not None
                        or cursor == 0
                        or content[cursor - 1] in "\r\n"
                    ),
                    terminal_line_complete=(
                        preferred == len(content) or content[preferred] in "\r\n"
                    ),
                )
                preferred_suffix = _closing_suffix(
                    prefix + content[cursor:preferred], preferred_fence
                )
                if len(prefix) + (preferred - cursor) + len(preferred_suffix) <= limit:
                    end = preferred
                    next_fence = preferred_fence
                    suffix = preferred_suffix

        chunks.append(prefix + content[cursor:end] + suffix)
        cursor = end
        fence = next_fence

    return tuple(chunks)


def markdown_post_content(markdown: str) -> str:
    return _json_string(
        {
            "zh_cn": {
                "title": "",
                "content": [[{"tag": "md", "text": markdown}]],
            }
        }
    )


def prepare_attachments(
    workspace: Path,
    attachments: tuple[OutboundAttachment, ...],
) -> tuple[PreparedAttachment, ...]:
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Lark attachment workspace must be a directory")

    prepared: list[PreparedAttachment] = []
    for descriptor in attachments:
        if len(descriptor.name.encode("utf-8")) > MAX_FILENAME_BYTES:
            raise ValueError(
                f"Lark attachment filename exceeds 256 bytes: {descriptor.name}"
            )
        media_type = descriptor.media_type or mimetypes.guess_type(descriptor.name)[0]
        media_type = media_type or "application/octet-stream"
        if "\r" in media_type or "\n" in media_type:
            raise ValueError("Lark attachment media type must not contain line breaks")

        message_type = _message_type(descriptor)
        maximum_size = MAX_IMAGE_BYTES if message_type == "image" else MAX_FILE_BYTES
        descriptor_fd, file_stat = _open_regular_file(root, descriptor)
        digest = hashlib.sha256()
        actual_size = 0
        try:
            while chunk := os.read(descriptor_fd, CHUNK_SIZE):
                actual_size += len(chunk)
                if actual_size > maximum_size:
                    raise ValueError(
                        f"Lark {message_type} attachment exceeds its size limit: "
                        f"{descriptor.name}"
                    )
                digest.update(chunk)
        finally:
            os.close(descriptor_fd)

        actual_digest = digest.hexdigest()
        if actual_size != descriptor.size_bytes:
            raise ValueError(f"Lark attachment size changed: {descriptor.name}")
        if actual_digest != descriptor.sha256:
            raise ValueError(f"Lark attachment digest mismatch: {descriptor.name}")
        if actual_size == 0:
            raise ValueError(f"Lark attachment must not be empty: {descriptor.name}")

        prepared.append(
            PreparedAttachment(
                descriptor=descriptor,
                workspace=root,
                media_type=media_type,
                message_type=message_type,
                file_type=_file_type(descriptor),
                size_bytes=actual_size,
                sha256=actual_digest,
                device=file_stat.st_dev,
                inode=file_stat.st_ino,
            )
        )
    return tuple(prepared)


async def send_outbound(
    api: LarkApi,
    *,
    identity: LarkBotIdentity,
    workspace: Path,
    request: ChannelSendRequest,
    timeout: float,
) -> ProviderCallResult[ChannelDeliveryReceipt]:
    try:
        thread = parse_provider_thread_id(
            request.provider_thread_id,
            bot_open_id=identity.open_id,
        )
    except ValueError as error:
        return _failed("invalid_provider_thread", str(error))

    if thread.thread_id != "0" and request.provider_reply_to_message_id is None:
        return _failed(
            "missing_thread_anchor",
            "Lark topic outbound delivery requires a reply anchor",
        )

    try:
        body_parts = split_markdown(request.body) if request.body.strip() else ()
    except ValueError as error:
        return _failed("invalid_markdown", str(error))

    total_parts = len(body_parts) + len(request.attachments)
    if total_parts == 0:
        return _failed("empty_message", "Lark outbound message must not be empty")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        prepared = await asyncio.wait_for(
            asyncio.to_thread(
                prepare_attachments,
                workspace,
                request.attachments,
            ),
            timeout=max(0.0, deadline - loop.time()),
        )
    except TimeoutError:
        return _failed("delivery_timeout", "Lark attachment preflight timed out")
    except (OSError, ValueError) as error:
        return _failed("invalid_attachment", str(error))

    parts: list[dict[str, object]] = []
    uploads: list[dict[str, object]] = []
    confirmed = 0
    reply_anchor = request.provider_reply_to_message_id

    for body_part in body_parts:
        result = await _send_message_part(
            api,
            thread=thread,
            message_type="post",
            content=markdown_post_content(body_part),
            reply_anchor=reply_anchor,
            reply_in_thread=thread.thread_id != "0",
            deadline=deadline,
        )
        if result[0] is not None:
            provider_message_id = result[0]
            confirmed += 1
            parts.append(
                {
                    "ordinal": len(parts) + 1,
                    "message_type": "post",
                    "provider_message_id": provider_message_id,
                }
            )
            if thread.thread_id != "0":
                reply_anchor = provider_message_id
            else:
                reply_anchor = None
            continue
        return _finish_failure(
            result[1],
            total_parts=total_parts,
            confirmed=confirmed,
            parts=parts,
            uploads=uploads,
        )

    for attachment in prepared:
        if loop.time() >= deadline:
            return _finish_failure(
                TimeoutError("Lark delivery deadline expired"),
                total_parts=total_parts,
                confirmed=confirmed,
                parts=parts,
                uploads=uploads,
            )
        try:
            with attachment.open() as file:
                if attachment.message_type == "image":
                    provider_key = await api.upload_image(
                        file,
                        filename=attachment.descriptor.name,
                        media_type=attachment.media_type,
                        timeout=max(0.0, deadline - loop.time()),
                    )
                else:
                    provider_key = await api.upload_file(
                        file,
                        file_type=attachment.file_type,
                        filename=attachment.descriptor.name,
                        media_type=attachment.media_type,
                        timeout=max(0.0, deadline - loop.time()),
                    )
            uploads.append(
                {
                    "ordinal": len(uploads) + 1,
                    "name": attachment.descriptor.name,
                    "message_type": attachment.message_type,
                    "provider_key": provider_key,
                }
            )
            content_field = (
                "image_key" if attachment.message_type == "image" else "file_key"
            )
            result = await _send_message_part(
                api,
                thread=thread,
                message_type=attachment.message_type,
                content=_json_string({content_field: provider_key}),
                reply_anchor=reply_anchor,
                reply_in_thread=thread.thread_id != "0",
                deadline=deadline,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            return _finish_failure(
                error,
                total_parts=total_parts,
                confirmed=confirmed,
                parts=parts,
                uploads=uploads,
            )

        if result[0] is None:
            return _finish_failure(
                result[1],
                total_parts=total_parts,
                confirmed=confirmed,
                parts=parts,
                uploads=uploads,
            )
        provider_message_id = result[0]
        confirmed += 1
        parts.append(
            {
                "ordinal": len(parts) + 1,
                "message_type": attachment.message_type,
                "provider_message_id": provider_message_id,
                "attachment_name": attachment.descriptor.name,
            }
        )
        if thread.thread_id != "0":
            reply_anchor = provider_message_id
        else:
            reply_anchor = None

    receipt = _delivery_receipt(total_parts, confirmed, parts, uploads)
    last_message_id = str(parts[-1]["provider_message_id"])
    return ProviderCallResult(
        status=ProviderCallStatus.CONFIRMED,
        value=ChannelDeliveryReceipt(provider_message_id=last_message_id),
        receipt=receipt,
    )


async def _send_message_part(
    api: LarkApi,
    *,
    thread: LarkThreadIdentity,
    message_type: str,
    content: str,
    reply_anchor: str | None,
    reply_in_thread: bool,
    deadline: float,
) -> tuple[str | None, Exception | None]:
    timeout = max(0.0, deadline - asyncio.get_running_loop().time())
    if timeout <= 0:
        return None, TimeoutError("Lark delivery deadline expired")
    try:
        if reply_anchor is not None:
            provider_message_id = await api.reply_message(
                message_id=reply_anchor,
                message_type=message_type,
                content=content,
                reply_in_thread=reply_in_thread,
                uuid=str(uuid4()),
                timeout=timeout,
            )
        else:
            provider_message_id = await api.send_message(
                chat_id=thread.chat_id,
                message_type=message_type,
                content=content,
                uuid=str(uuid4()),
                timeout=timeout,
            )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        return None, error
    return provider_message_id, None


def _finish_failure(
    error: Exception | None,
    *,
    total_parts: int,
    confirmed: int,
    parts: list[dict[str, object]],
    uploads: list[dict[str, object]],
) -> ProviderCallResult[ChannelDeliveryReceipt]:
    if error is None:
        error = RuntimeError("Lark outbound part failed without an error")
    unknown = _is_unknown(error)
    if unknown:
        status = ProviderCallStatus.UNKNOWN
    elif confirmed:
        status = ProviderCallStatus.PARTIAL
    else:
        status = ProviderCallStatus.FAILED
    error_kind, error_message = _error_details(error)
    receipt = _delivery_receipt(total_parts, confirmed, parts, uploads)
    if status is ProviderCallStatus.PARTIAL:
        last_message_id = str(parts[-1]["provider_message_id"])
        return ProviderCallResult(
            status=status,
            value=ChannelDeliveryReceipt(provider_message_id=last_message_id),
            error_kind=error_kind,
            error_message=error_message,
            receipt=receipt,
        )
    return ProviderCallResult(
        status=status,
        error_kind=error_kind,
        error_message=error_message,
        receipt=receipt,
    )


def _failed(
    error_kind: str, error_message: str
) -> ProviderCallResult[ChannelDeliveryReceipt]:
    return ProviderCallResult(
        status=ProviderCallStatus.FAILED,
        error_kind=error_kind,
        error_message=error_message,
    )


def _delivery_receipt(
    total_parts: int,
    confirmed: int,
    parts: list[dict[str, object]],
    uploads: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "total_parts": total_parts,
        "confirmed_parts": confirmed,
        "parts": list(parts),
        "uploads": list(uploads),
    }


def _is_unknown(error: Exception) -> bool:
    if isinstance(error, (LarkTransportError, TimeoutError)):
        return True
    if isinstance(error, LarkApiError):
        return error.provider_code == 0 or (
            error.provider_code is None
            and error.message == "provider returned invalid JSON"
        )
    return False


def _error_details(error: Exception) -> tuple[str, str]:
    if isinstance(error, LarkApiError):
        return f"lark_provider_{error.method}", error.message
    if isinstance(error, LarkTransportError):
        return f"lark_transport_{error.method}", error.error_kind
    if isinstance(error, TimeoutError):
        return "delivery_timeout", str(error) or "Lark delivery timed out"
    if isinstance(error, (OSError, ValueError)):
        return "invalid_attachment", str(error)
    return type(error).__name__, str(error) or "Lark outbound delivery failed"


def _message_type(descriptor: OutboundAttachment) -> str:
    suffix = PurePosixPath(descriptor.name).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _AUDIO_SUFFIXES:
        return "audio"
    if suffix in _VIDEO_SUFFIXES:
        return "media"
    return "file"


def _file_type(descriptor: OutboundAttachment) -> str:
    suffix = PurePosixPath(descriptor.name).suffix.lower()
    if suffix in _AUDIO_SUFFIXES:
        return "opus"
    if suffix in _VIDEO_SUFFIXES:
        return "mp4"
    return _DOCUMENT_FILE_TYPES.get(suffix, "stream")


def _open_regular_file(
    workspace: Path,
    attachment: OutboundAttachment,
) -> tuple[int, os.stat_result]:
    relative = PurePosixPath(attachment.relative_path)
    current = workspace
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(
                f"Lark attachment path must not contain symlinks: {attachment.name}"
            )
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise ValueError(
            f"Lark attachment path leaves the workspace: {attachment.name}"
        ) from error
    descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        file_stat = os.fstat(descriptor)
        opened_stat = os.stat(resolved, follow_symlinks=False)
        if (
            file_stat.st_dev != opened_stat.st_dev
            or file_stat.st_ino != opened_stat.st_ino
        ):
            raise ValueError(
                f"Lark attachment changed while it was opened: {attachment.name}"
            )
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(
                f"Lark attachment must be a regular file: {attachment.name}"
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, file_stat


@dataclass(frozen=True, slots=True)
class _Fence:
    marker: str
    opening: str


def _preferred_boundary(content: str, minimum: int, end: int) -> int | None:
    for separator in ("\n\n", "\n"):
        index = content.rfind(separator, minimum, end)
        if index >= minimum:
            return index + len(separator)
    for index in range(end - 1, minimum - 1, -1):
        if content[index].isspace():
            return index + 1
    return None


def _advance_fence(
    fence: _Fence | None,
    segment: str,
    *,
    initial_line_boundary: bool,
    terminal_line_complete: bool,
) -> _Fence | None:
    lines = segment.splitlines(keepends=True)
    for index, line in enumerate(lines):
        complete = (index > 0 or initial_line_boundary) and (
            line.endswith(("\n", "\r"))
            or (index == len(lines) - 1 and terminal_line_complete)
        )
        if not complete:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if fence is None:
            marker_char = stripped[0]
            if marker_char not in {"`", "~"}:
                continue
            marker_length = len(stripped) - len(stripped.lstrip(marker_char))
            if marker_length < 3:
                continue
            marker = marker_char * marker_length
            info = stripped[marker_length:]
            if marker_char == "`" and "`" in info:
                continue
            fence = _Fence(marker=marker, opening=stripped)
            continue
        if stripped[0] != fence.marker[0]:
            continue
        marker_length = len(stripped) - len(stripped.lstrip(fence.marker[0]))
        if marker_length >= len(fence.marker) and not stripped[marker_length:].strip():
            fence = None
    return fence


def _closing_suffix(content: str, fence: _Fence | None) -> str:
    if fence is None:
        return ""
    separator = "" if content.endswith(("\n", "\r")) else "\n"
    return f"{separator}{fence.marker}"


def _json_string(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "CHUNK_SIZE",
    "MAX_FILENAME_BYTES",
    "MAX_FILE_BYTES",
    "MAX_IMAGE_BYTES",
    "MAX_MARKDOWN_CODEPOINTS",
    "PreparedAttachment",
    "markdown_post_content",
    "prepare_attachments",
    "send_outbound",
    "split_markdown",
]
