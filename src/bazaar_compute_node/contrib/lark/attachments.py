from __future__ import annotations

import asyncio
import json
import mimetypes
from collections import OrderedDict
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from email.message import Message
from time import monotonic

from ...core.channel import IAttachmentMaterializer
from ...core.models import InboundAttachment
from .api import LarkApi

_RESOURCE_TYPES = frozenset({"image", "file", "audio", "media", "sticker"})
_RESOURCE_DOWNLOAD_TYPES = {
    "image": "image",
    "file": "file",
    "audio": "file",
    "media": "file",
}
_MAX_RESOURCE_BYTES = 25 * 1024 * 1024
_MAX_RESOURCE_CACHE_ENTRIES = 256
_RESOURCE_CACHE_TTL_SECONDS = 5 * 60
_RESOURCE_CHUNK_BYTES = 64 * 1024
_MAX_CONTENT_DEPTH = 32


@dataclass(frozen=True, slots=True)
class LarkMention:
    key: str
    open_id: str | None
    display_name: str


@dataclass(frozen=True, slots=True)
class LarkResourceDescriptor:
    file_key: str | None
    resource_type: str
    name: str
    media_type: str | None = None

    def __post_init__(self) -> None:
        if self.resource_type not in _RESOURCE_TYPES:
            raise ValueError("unsupported Lark resource type")
        if not self.name:
            raise ValueError("Lark resource name must be non-empty")
        if self.file_key is not None and (
            not self.file_key or "\r" in self.file_key or "\n" in self.file_key
        ):
            raise ValueError("Lark resource file_key must be valid text")
        if self.media_type is not None and (
            not self.media_type or "\r" in self.media_type or "\n" in self.media_type
        ):
            raise ValueError("Lark resource media_type must be valid text")

    @property
    def placeholder(self) -> str:
        return f"[{self.resource_type}: {self.name}]"


@dataclass(frozen=True, slots=True)
class LarkContentProjection:
    message_type: str
    body: str
    resources: tuple[LarkResourceDescriptor, ...] = ()
    content_error: bool = False


def project_lark_content(
    message_type: str,
    content: object,
    *,
    mentions: Mapping[str, LarkMention],
    bot_open_id: str,
) -> LarkContentProjection:
    content_value, content_error = _decode_content(content)
    if message_type == "text":
        text = content_value.get("text") if isinstance(content_value, Mapping) else None
        if not isinstance(text, str):
            return LarkContentProjection(
                message_type="text",
                body="[invalid lark content: text]",
                content_error=True,
            )
        return LarkContentProjection(
            message_type="text",
            body=_replace_mentions(text, mentions, bot_open_id).strip(),
            content_error=content_error,
        )

    if message_type == "post":
        resources: list[LarkResourceDescriptor] = []
        body = _render_post(
            _select_post_locale(content_value),
            mentions=mentions,
            bot_open_id=bot_open_id,
            resources=resources,
            depth=0,
        ).strip()
        return LarkContentProjection(
            message_type="post",
            body=body or "[lark post]",
            resources=tuple(_unique_resources(resources)),
            content_error=content_error,
        )

    if message_type in _RESOURCE_TYPES or message_type == "video":
        resource_type = "media" if message_type == "video" else message_type
        resource = _resource_from_mapping(content_value, resource_type)
        body = resource.placeholder
        return LarkContentProjection(
            message_type=message_type,
            body=body,
            resources=(resource,),
            content_error=content_error,
        )

    return LarkContentProjection(
        message_type=f"unsupported:{message_type}",
        body=f"[unsupported lark message type: {message_type}]",
        content_error=content_error,
    )


class LarkResourceCache:
    def __init__(self, materializer: IAttachmentMaterializer) -> None:
        self._materializer = materializer
        self._cache: OrderedDict[
            tuple[str, str, str], tuple[float, InboundAttachment]
        ] = OrderedDict()
        self._inflight: dict[tuple[str, str, str], asyncio.Task[InboundAttachment]] = {}
        self._lock = asyncio.Lock()

    async def materialize(
        self,
        api: LarkApi,
        *,
        provider_message_id: str,
        resource: LarkResourceDescriptor,
        timeout: float,
    ) -> InboundAttachment:
        if resource.file_key is None:
            return self._materializer.failed(
                name=resource.name,
                kind=resource.resource_type,
                error="missing_resource_key",
                media_type=resource.media_type,
            )

        cache_key = (
            provider_message_id,
            resource.file_key,
            resource.resource_type,
        )
        now = monotonic()
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                expires_at, attachment = cached
                if expires_at > now:
                    self._cache.move_to_end(cache_key)
                    return attachment
                self._cache.pop(cache_key, None)
            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._download_and_cache(
                        api,
                        cache_key,
                        provider_message_id,
                        resource,
                        timeout,
                    ),
                    name="bcn-lark-resource",
                )
                self._inflight[cache_key] = task
        return await asyncio.shield(task)

    async def close(self) -> None:
        async with self._lock:
            tasks = tuple(self._inflight.values())
            self._inflight.clear()
            self._cache.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _download_and_cache(
        self,
        api: LarkApi,
        cache_key: tuple[str, str, str],
        provider_message_id: str,
        resource: LarkResourceDescriptor,
        timeout: float,
    ) -> InboundAttachment:
        attachment: InboundAttachment | None = None
        try:
            attachment = await self._download(
                api,
                provider_message_id=provider_message_id,
                resource=resource,
                timeout=timeout,
            )
            return attachment
        finally:
            async with self._lock:
                self._inflight.pop(cache_key, None)
                if attachment is not None:
                    self._cache[cache_key] = (
                        monotonic() + _RESOURCE_CACHE_TTL_SECONDS,
                        attachment,
                    )
                    self._cache.move_to_end(cache_key)
                    while len(self._cache) > _MAX_RESOURCE_CACHE_ENTRIES:
                        self._cache.popitem(last=False)

    async def _download(
        self,
        api: LarkApi,
        *,
        provider_message_id: str,
        resource: LarkResourceDescriptor,
        timeout: float,
    ) -> InboundAttachment:
        name = resource.name
        media_type = resource.media_type
        download_type = _resource_download_type(resource.resource_type)
        if download_type is None:
            return self._materializer.failed(
                name=name,
                kind=resource.resource_type,
                error="resource_download_unsupported",
                media_type=media_type,
            )
        try:
            async with api.open_message_resource(
                provider_message_id,
                resource.file_key or "",
                download_type,
                timeout=timeout,
            ) as response:
                content_length = response.content_length
                if content_length is not None and (
                    content_length < 0 or content_length > _MAX_RESOURCE_BYTES
                ):
                    raise ValueError("resource_too_large")
                name = _safe_filename(
                    name,
                    fallback=resource.resource_type,
                    resource_type=resource.resource_type,
                )
                media_type = _safe_media_type(
                    response.headers.get("Content-Type"),
                    fallback=media_type,
                )
                name = _resolve_resource_name(
                    event_name=name,
                    response_name=_content_disposition_filename(
                        response.headers.get("Content-Disposition")
                    ),
                    media_type=media_type,
                    resource_type=resource.resource_type,
                )
                return await self._materializer.materialize(
                    _bounded_stream(
                        response.content.iter_chunked(_RESOURCE_CHUNK_BYTES)
                    ),
                    name=name,
                    kind=resource.resource_type,
                    media_type=media_type,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            return self._materializer.failed(
                name=name,
                kind=resource.resource_type,
                error=_resource_error(error),
                media_type=media_type,
            )


async def _bounded_stream(source: AsyncIterable[bytes]) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in source:
        if not isinstance(chunk, bytes):
            raise TypeError("Lark resource stream must yield bytes")
        total += len(chunk)
        if total > _MAX_RESOURCE_BYTES:
            raise ValueError("resource_too_large")
        yield chunk


def _decode_content(content: object) -> tuple[object, bool]:
    if isinstance(content, Mapping):
        return content, False
    if not isinstance(content, str):
        return {}, True
    try:
        decoded = json.loads(content)
    except TypeError:
        return {}, True
    except ValueError:
        return {}, True
    return decoded, not isinstance(decoded, Mapping)


def _select_post_locale(content: object) -> object:
    if not isinstance(content, Mapping):
        return content
    if "title" in content or "content" in content:
        return content
    for locale in ("zh_cn", "en_us", "ja_jp", "ko_kr"):
        value = content.get(locale)
        if isinstance(value, (Mapping, list, tuple)):
            return value
    for value in content.values():
        if isinstance(value, (Mapping, list, tuple)):
            return value
    return content


def _render_post(
    value: object,
    *,
    mentions: Mapping[str, LarkMention],
    bot_open_id: str,
    resources: list[LarkResourceDescriptor],
    depth: int,
) -> str:
    if depth > _MAX_CONTENT_DEPTH:
        return "[nested lark content]"
    if isinstance(value, str):
        return _replace_mentions(value, mentions, bot_open_id)
    if isinstance(value, (list, tuple)):
        return "".join(
            _render_post(
                child,
                mentions=mentions,
                bot_open_id=bot_open_id,
                resources=resources,
                depth=depth + 1,
            )
            for child in value
        )
    if not isinstance(value, Mapping):
        return ""

    tag = value.get("tag")
    if isinstance(tag, str):
        if tag in {"text", "code", "pre", "del", "bold", "underline", "italic"}:
            return _replace_mentions(
                _string_value(value.get("text")), mentions, bot_open_id
            )
        if tag in {"a", "link"}:
            label = _render_post(
                value.get("text") or value.get("content") or "",
                mentions=mentions,
                bot_open_id=bot_open_id,
                resources=resources,
                depth=depth + 1,
            )
            href = value.get("href") or value.get("url")
            if isinstance(href, str) and href:
                return f"{label} ({href})" if label else href
            return label
        if tag == "at":
            return _render_mention_node(value, mentions, bot_open_id)
        resource_type = _resource_type_for_tag(tag)
        if resource_type is not None:
            resource = _resource_from_mapping(value, resource_type)
            resources.append(resource)
            return resource.placeholder

        children = value.get("content")
        if children is None:
            children = value.get("children")
        if children is None:
            children = value.get("text")
        rendered = _render_post(
            children,
            mentions=mentions,
            bot_open_id=bot_open_id,
            resources=resources,
            depth=depth + 1,
        )
        if tag in {
            "title",
            "paragraph",
            "p",
            "li",
            "list",
            "blockquote",
            "quote",
        }:
            return f"{rendered}\n"
        return rendered

    parts: list[str] = []
    title = value.get("title")
    if title is not None:
        parts.append(
            _render_post(
                title,
                mentions=mentions,
                bot_open_id=bot_open_id,
                resources=resources,
                depth=depth + 1,
            )
        )
    content = value.get("content")
    if content is not None:
        parts.append(
            _render_post(
                content,
                mentions=mentions,
                bot_open_id=bot_open_id,
                resources=resources,
                depth=depth + 1,
            )
        )
    if parts:
        return "\n".join(parts)
    text = value.get("text")
    if isinstance(text, str):
        return _replace_mentions(text, mentions, bot_open_id)
    return ""


def _render_mention_node(
    node: Mapping[str, object],
    mentions: Mapping[str, LarkMention],
    bot_open_id: str,
) -> str:
    key = node.get("key")
    mention = mentions.get(key) if isinstance(key, str) else None
    if mention is None:
        open_id = node.get("user_id") or node.get("open_id")
        if isinstance(open_id, str):
            mention = next(
                (item for item in mentions.values() if item.open_id == open_id),
                None,
            )
            if mention is None:
                if open_id == bot_open_id:
                    return ""
                return f"@{open_id}"
    if mention is not None:
        return "" if mention.open_id == bot_open_id else f"@{mention.display_name}"
    text = node.get("text")
    return text if isinstance(text, str) else "@user"


def _resource_from_mapping(
    value: object,
    resource_type: str,
) -> LarkResourceDescriptor:
    mapping = value if isinstance(value, Mapping) else {}
    key = None
    for field_name in (
        "file_key",
        "image_key",
        "audio_key",
        "media_key",
        "sticker_key",
        "resource_key",
        "key",
    ):
        candidate = mapping.get(field_name)
        if isinstance(candidate, str) and candidate:
            key = candidate
            break
    raw_name = None
    for field_name in ("file_name", "name", "filename", "alt", "text"):
        candidate = mapping.get(field_name)
        if isinstance(candidate, str) and candidate:
            raw_name = candidate
            break
    name = _safe_filename(
        raw_name,
        fallback=resource_type,
        resource_type=resource_type,
    )
    media_type = _safe_media_type(mapping.get("mime_type"))
    return LarkResourceDescriptor(
        file_key=key,
        resource_type=resource_type,
        name=name,
        media_type=media_type,
    )


def _resource_type_for_tag(tag: str) -> str | None:
    if tag == "img" or tag == "image":
        return "image"
    if tag == "video":
        return "media"
    return tag if tag in _RESOURCE_TYPES else None


def _resource_download_type(resource_type: str) -> str | None:
    return _RESOURCE_DOWNLOAD_TYPES.get(resource_type)


def _unique_resources(
    resources: list[LarkResourceDescriptor],
) -> list[LarkResourceDescriptor]:
    unique: list[LarkResourceDescriptor] = []
    seen: set[tuple[str | None, str]] = set()
    for resource in resources:
        key = (resource.file_key, resource.resource_type)
        if key in seen:
            continue
        seen.add(key)
        unique.append(resource)
    return unique


def _replace_mentions(
    value: str,
    mentions: Mapping[str, LarkMention],
    bot_open_id: str,
) -> str:
    rendered = value
    for key, mention in sorted(mentions.items(), key=lambda item: -len(item[0])):
        replacement = (
            "" if mention.open_id == bot_open_id else f"@{mention.display_name}"
        )
        rendered = rendered.replace(key, replacement)
    return rendered


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _content_disposition_filename(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\r" in value or "\n" in value:
        return None
    header = Message()
    header["Content-Disposition"] = value
    with suppress(ValueError):
        filename = header.get_filename()
        if filename:
            return filename
    return None


def _safe_filename(
    value: object,
    *,
    fallback: str,
    resource_type: str,
) -> str:
    candidate = value if isinstance(value, str) and value else fallback
    if not _is_safe_filename(candidate):
        candidate = fallback
    return candidate if _is_safe_filename(candidate) else resource_type


def _resolve_resource_name(
    *,
    event_name: str,
    response_name: str | None,
    media_type: str | None,
    resource_type: str,
) -> str:
    response_name = response_name if _is_safe_filename(response_name) else None
    if _safe_suffix(event_name):
        name = event_name
    elif response_name is not None and _safe_suffix(response_name):
        name = response_name
    elif event_name != resource_type:
        name = event_name
    elif response_name is not None:
        name = response_name
    else:
        name = event_name

    if _safe_suffix(name):
        return name
    suffix = _suffix_from_media_type(media_type)
    if not suffix and resource_type == "audio":
        suffix = ".mp3"
    return f"{name}{suffix}" if suffix else name


def _is_safe_filename(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 255
        and "/" not in value
        and "\\" not in value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _safe_suffix(name: str) -> str:
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    suffix = name[dot:]
    return (
        suffix
        if 1 < len(suffix) <= 11 and suffix[1:].isascii() and suffix[1:].isalnum()
        else ""
    )


def _suffix_from_media_type(media_type: str | None) -> str:
    media_type = (media_type or "").partition(";")[0].strip().lower()
    if not media_type or media_type.endswith("/octet-stream"):
        return ""
    suffix = mimetypes.guess_extension(media_type) or ""
    return (
        suffix
        if 1 < len(suffix) <= 11 and suffix[1:].isascii() and suffix[1:].isalnum()
        else ""
    )


def _safe_media_type(value: object, *, fallback: str | None = None) -> str | None:
    if not isinstance(value, str) or not value:
        return fallback
    if len(value) > 256 or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return fallback
    return value


def _resource_error(error: Exception) -> str:
    if isinstance(error, ValueError) and str(error) == "resource_too_large":
        return "resource_too_large"
    if isinstance(error, ValueError) and str(error) == "missing_resource_key":
        return "missing_resource_key"
    return "resource_download_failed"


__all__ = [
    "LarkContentProjection",
    "LarkMention",
    "LarkResourceCache",
    "LarkResourceDescriptor",
    "project_lark_content",
]
