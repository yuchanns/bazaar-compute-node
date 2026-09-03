from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from ...core.channel import ChannelDeliveryReceipt, ChannelSendRequest
from ...core.models import OutboundAttachment
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.utils.clock import remaining
from ...core.utils.markdown import split_markdown
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


@dataclass(slots=True)
class _Delivery:
    """What a multi-part Lark send has confirmed so far."""

    total_parts: int
    reply_anchor: str | None
    in_thread: bool
    confirmed: int = 0
    parts: list[dict[str, object]] = field(default_factory=list)
    uploads: list[dict[str, object]] = field(default_factory=list)

    def record(
        self, provider_message_id: str, message_type: str, **extra: object
    ) -> None:
        self.confirmed += 1
        self.parts.append(
            {
                "ordinal": len(self.parts) + 1,
                "message_type": message_type,
                "provider_message_id": provider_message_id,
                **extra,
            }
        )
        self.reply_anchor = provider_message_id if self.in_thread else None

    def receipt(self) -> dict[str, object]:
        return _delivery_receipt(
            self.total_parts, self.confirmed, self.parts, self.uploads
        )

    def failed(
        self, error: Exception | None
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        return _finish_failure(
            error,
            total_parts=self.total_parts,
            confirmed=self.confirmed,
            parts=self.parts,
            uploads=self.uploads,
        )


async def _upload(api: LarkApi, attachment: PreparedAttachment, deadline: float) -> str:
    """Put an attachment's bytes on the provider and get back its key."""

    with attachment.open() as file:
        if attachment.message_type == "image":
            return await api.upload_image(
                file,
                filename=attachment.descriptor.name,
                media_type=attachment.media_type,
                timeout=remaining(deadline),
            )
        return await api.upload_file(
            file,
            file_type=attachment.file_type,
            filename=attachment.descriptor.name,
            media_type=attachment.media_type,
            timeout=remaining(deadline),
        )


async def _send_body_parts(
    api: LarkApi,
    delivery: _Delivery,
    *,
    thread: LarkThreadIdentity,
    body_parts: tuple[str, ...],
    deadline: float,
) -> ProviderCallResult[ChannelDeliveryReceipt] | None:
    """Send the message text, in as many parts as it had to be split into."""

    for body_part in body_parts:
        provider_message_id, error = await _send_message_part(
            api,
            thread=thread,
            message_type="post",
            content=markdown_post_content(body_part),
            reply_anchor=delivery.reply_anchor,
            reply_in_thread=delivery.in_thread,
            deadline=deadline,
        )
        if provider_message_id is None:
            return delivery.failed(error)
        delivery.record(provider_message_id, "post")
    return None


async def _send_attachments(
    api: LarkApi,
    delivery: _Delivery,
    *,
    thread: LarkThreadIdentity,
    prepared: tuple[PreparedAttachment, ...],
    deadline: float,
) -> ProviderCallResult[ChannelDeliveryReceipt] | None:
    """Upload each attachment and send the message that carries it."""

    for attachment in prepared:
        if remaining(deadline) <= 0:
            return delivery.failed(TimeoutError("Lark delivery deadline expired"))
        try:
            provider_key = await _upload(api, attachment, deadline)
            delivery.uploads.append(
                {
                    "ordinal": len(delivery.uploads) + 1,
                    "name": attachment.descriptor.name,
                    "message_type": attachment.message_type,
                    "provider_key": provider_key,
                }
            )
            content_field = (
                "image_key" if attachment.message_type == "image" else "file_key"
            )
            provider_message_id, error = await _send_message_part(
                api,
                thread=thread,
                message_type=attachment.message_type,
                content=_json_string({content_field: provider_key}),
                reply_anchor=delivery.reply_anchor,
                reply_in_thread=delivery.in_thread,
                deadline=deadline,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            return delivery.failed(error)

        if provider_message_id is None:
            return delivery.failed(error)
        delivery.record(
            provider_message_id,
            attachment.message_type,
            attachment_name=attachment.descriptor.name,
        )
    return None


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
        body_parts = (
            split_markdown(request.body, limit=MAX_MARKDOWN_CODEPOINTS)
            if request.body.strip()
            else ()
        )
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
            timeout=remaining(deadline),
        )
    except TimeoutError:
        return _failed("delivery_timeout", "Lark attachment preflight timed out")
    except (OSError, ValueError) as error:
        return _failed("invalid_attachment", str(error))

    delivery = _Delivery(
        total_parts=total_parts,
        reply_anchor=request.provider_reply_to_message_id,
        in_thread=thread.thread_id != "0",
    )

    failure = await _send_body_parts(
        api, delivery, thread=thread, body_parts=body_parts, deadline=deadline
    )
    if failure is None:
        failure = await _send_attachments(
            api, delivery, thread=thread, prepared=prepared, deadline=deadline
        )
    if failure is not None:
        return failure

    last_message_id = str(delivery.parts[-1]["provider_message_id"])
    return ProviderCallResult(
        status=ProviderCallStatus.CONFIRMED,
        value=ChannelDeliveryReceipt(provider_message_id=last_message_id),
        receipt=delivery.receipt(),
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
    open_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    binary_flag = getattr(os, "O_BINARY", None)
    if binary_flag is not None:
        open_flags |= binary_flag
    descriptor = os.open(resolved, open_flags)
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
