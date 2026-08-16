from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Mapping
from dataclasses import dataclass

from ...core.channel import IAttachmentMaterializer
from ...core.models import InboundAttachment
from .api import TelegramApiError, TelegramBotApi, TelegramTransportError

_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
_MAX_RICH_BLOCKS = 500
_MAX_RICH_DEPTH = 16


@dataclass(frozen=True, slots=True)
class TelegramAttachmentSource:
    file_id: str
    kind: str
    name: str
    media_type: str | None = None
    file_size: int | None = None
    file_unique_id: str | None = None

    def __post_init__(self) -> None:
        if not self.file_id or not self.kind or not self.name:
            raise ValueError(
                "Telegram attachment source requires file_id, kind, and name"
            )
        if self.file_size is not None and (
            isinstance(self.file_size, bool)
            or not isinstance(self.file_size, int)
            or self.file_size < 0
        ):
            raise ValueError("Telegram attachment file_size must be non-negative")


def attachment_sources(
    message: Mapping[str, object],
) -> tuple[TelegramAttachmentSource, ...]:
    message_id = message.get("message_id")
    message_label = str(message_id) if isinstance(message_id, int) else "message"
    sources: list[TelegramAttachmentSource] = []

    photo = message.get("photo")
    selected_photo = _largest_photo(photo)
    if selected_photo is not None:
        source = _source_from_file(
            selected_photo,
            kind="photo",
            fallback_name=f"photo-{message_label}.jpg",
            default_media_type="image/jpeg",
        )
        if source is not None:
            sources.append(source)

    animation = message.get("animation")
    for field_name, kind, fallback_suffix, default_media_type in (
        ("video", "video", "mp4", "video/mp4"),
        ("animation", "animation", "mp4", "video/mp4"),
        ("audio", "audio", "mp3", "audio/mpeg"),
        ("voice", "voice", "ogg", "audio/ogg"),
        ("video_note", "video_note", "mp4", "video/mp4"),
        ("document", "document", "bin", None),
    ):
        value = message.get(field_name)
        if not isinstance(value, Mapping):
            continue
        if field_name == "document" and isinstance(animation, Mapping):
            animation_file_id = animation.get("file_id")
            if isinstance(animation_file_id, str) and animation_file_id == value.get(
                "file_id"
            ):
                continue
        source = _source_from_file(
            value,
            kind=kind,
            fallback_name=f"{kind}-{message_label}.{fallback_suffix}",
            default_media_type=default_media_type,
        )
        if source is not None:
            sources.append(source)

    rich_message = message.get("rich_message")
    if isinstance(rich_message, Mapping):
        blocks = rich_message.get("blocks")
        if isinstance(blocks, list):
            _collect_rich_media(
                blocks,
                sources,
                message_label=message_label,
                depth=0,
                remaining=[_MAX_RICH_BLOCKS],
            )

    return tuple(sources)


async def materialize_attachments(
    api: TelegramBotApi,
    materializer: IAttachmentMaterializer,
    sources: tuple[TelegramAttachmentSource, ...],
) -> tuple[InboundAttachment, ...]:
    attachments: list[InboundAttachment] = []
    for source in sources:
        if source.file_size is not None and source.file_size > _MAX_DOWNLOAD_BYTES:
            attachments.append(
                materializer.failed(
                    name=source.name,
                    kind=source.kind,
                    media_type=source.media_type,
                    error="telegram_file_too_large",
                )
            )
            continue
        try:
            provider_file = await api.get_file(source.file_id)
            file_path = provider_file.get("file_path")
            if not isinstance(file_path, str) or not file_path:
                raise ValueError("Telegram getFile omitted file_path")
            provider_size = provider_file.get("file_size")
            if (
                isinstance(provider_size, int)
                and not isinstance(provider_size, bool)
                and provider_size > _MAX_DOWNLOAD_BYTES
            ):
                raise ValueError("Telegram file exceeds download limit")
            attachment = await materializer.materialize(
                _bounded_stream(api.download_file(file_path)),
                name=source.name,
                kind=source.kind,
                media_type=source.media_type,
            )
        except asyncio.CancelledError:
            raise
        except (
            TelegramApiError,
            TelegramTransportError,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            attachment = materializer.failed(
                name=source.name,
                kind=source.kind,
                media_type=source.media_type,
                error=f"telegram_attachment_failed:{type(error).__name__}",
            )
        attachments.append(attachment)
    return tuple(attachments)


async def _bounded_stream(source: AsyncIterable[bytes]):
    total = 0
    async for chunk in source:
        if not isinstance(chunk, bytes):
            raise TypeError("Telegram file stream must yield bytes")
        total += len(chunk)
        if total > _MAX_DOWNLOAD_BYTES:
            raise ValueError("Telegram file exceeds download limit")
        yield chunk


def _collect_rich_media(
    blocks: list[object],
    sources: list[TelegramAttachmentSource],
    *,
    message_label: str,
    depth: int,
    remaining: list[int],
) -> None:
    if depth > _MAX_RICH_DEPTH:
        return
    for block in blocks:
        if remaining[0] <= 0:
            return
        if not isinstance(block, Mapping):
            continue
        remaining[0] -= 1
        block_type = block.get("type")
        if block_type == "photo":
            selected = _largest_photo(block.get("photo"))
            if selected is not None:
                source = _source_from_file(
                    selected,
                    kind="photo",
                    fallback_name=f"photo-{message_label}.jpg",
                    default_media_type="image/jpeg",
                )
                if source is not None:
                    sources.append(source)
        elif block_type in {"video", "animation", "audio", "voice_note"}:
            field_name = "voice_note" if block_type == "voice_note" else str(block_type)
            value = block.get(field_name)
            if isinstance(value, Mapping):
                suffix, media_type = {
                    "video": ("mp4", "video/mp4"),
                    "animation": ("mp4", "video/mp4"),
                    "audio": ("mp3", "audio/mpeg"),
                    "voice_note": ("ogg", "audio/ogg"),
                }[str(block_type)]
                source = _source_from_file(
                    value,
                    kind=str(block_type),
                    fallback_name=f"{block_type}-{message_label}.{suffix}",
                    default_media_type=media_type,
                )
                if source is not None:
                    sources.append(source)

        nested_blocks: list[object] = []
        raw_blocks = block.get("blocks")
        if isinstance(raw_blocks, list):
            nested_blocks.extend(raw_blocks)
        items = block.get("items")
        if isinstance(items, list):
            for item in items:
                if remaining[0] <= 0:
                    break
                if not isinstance(item, Mapping):
                    continue
                remaining[0] -= 1
                if isinstance(item.get("blocks"), list):
                    nested_blocks.extend(item["blocks"])
        if nested_blocks:
            _collect_rich_media(
                nested_blocks,
                sources,
                message_label=message_label,
                depth=depth + 1,
                remaining=remaining,
            )


def _largest_photo(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, list):
        return None
    candidates = [item for item in value if isinstance(item, Mapping)]
    if not candidates:
        return None

    def score(item: Mapping[str, object]) -> tuple[int, int]:
        file_size = item.get("file_size")
        size_score = (
            file_size
            if isinstance(file_size, int)
            and not isinstance(file_size, bool)
            and file_size >= 0
            else -1
        )
        width = item.get("width")
        height = item.get("height")
        area = (
            width * height
            if isinstance(width, int)
            and not isinstance(width, bool)
            and isinstance(height, int)
            and not isinstance(height, bool)
            and width >= 0
            and height >= 0
            else -1
        )
        return size_score, area

    return max(candidates, key=score)


def _source_from_file(
    value: Mapping[str, object],
    *,
    kind: str,
    fallback_name: str,
    default_media_type: str | None,
) -> TelegramAttachmentSource | None:
    file_id = value.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        return None
    file_unique_id = value.get("file_unique_id")
    if not isinstance(file_unique_id, str) or not file_unique_id:
        file_unique_id = None
    file_name = value.get("file_name")
    name = file_name if isinstance(file_name, str) and file_name else fallback_name
    media_type = value.get("mime_type")
    if not isinstance(media_type, str) or not media_type:
        media_type = default_media_type
    file_size = value.get("file_size")
    if not isinstance(file_size, int) or isinstance(file_size, bool) or file_size < 0:
        file_size = None
    return TelegramAttachmentSource(
        file_id=file_id,
        file_unique_id=file_unique_id,
        kind=kind,
        name=name,
        media_type=media_type,
        file_size=file_size,
    )


__all__ = [
    "TelegramAttachmentSource",
    "attachment_sources",
    "materialize_attachments",
]
