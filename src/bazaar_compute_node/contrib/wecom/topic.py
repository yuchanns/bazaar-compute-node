from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import re
from collections.abc import Awaitable, Callable, Mapping
from time import time_ns
from urllib.parse import unquote
from uuid import NAMESPACE_URL, uuid5

import aiohttp
from pydantic import ValidationError
from yarl import URL

from ...core.channel import ChannelContext, ChannelDeliveryReceipt, ChannelSendRequest
from ...core.models import (
    ChannelTargetKind,
    ChannelTargetPresentation,
    InboundAttachment,
    Message,
    MessageDirection,
    SenderIdentity,
    SenderKind,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.utils.markdown import split_markdown, utf8_bytes
from .identity import TopicAddress, post_message_id, reply_message_id
from .outbound import AttachmentReader, PreparedAttachment, prepare_attachments
from .topic_api import (
    ApiResponse,
    Content,
    Download,
    Member,
    Post,
    Reply,
    SentPost,
    SentReply,
    TopicApiError,
    TopicClient,
    TopicEvent,
    Topics,
    UploadFinished,
    UploadStarted,
)

_CONTENT_BYTES = 20_480
_MARKDOWN_LITERAL = re.compile(
    r"(?m)^ {0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n.*?"
    r"^ {0,3}(?P=fence)[`~]*[ \t]*(?:\n|$)"
    r"|(?m:^ {4}[^\n]*(?:\n|$))"
    r"|(?P<ticks>`+)(?!`).*?(?<!`)(?P=ticks)(?!`)"
    r"|!?\[[^\]\n]*\]\([^\n]*?\)"
    r"|<[^>\n]+>",
    re.DOTALL,
)


def mention_content(
    body: str, members: tuple[Member, ...]
) -> tuple[str, list[dict[str, object]]]:
    """Turn explicit human handles outside Markdown literals into native mentions."""

    ids = {member.id for member in members if member.type == 0}
    if not ids:
        return body, []
    pattern = re.compile(
        r"(?<![A-Za-z0-9_@\\])@("
        + "|".join(re.escape(id) for id in sorted(ids, key=len, reverse=True))
        + r")(?![A-Za-z0-9_.-])"
    )
    mentions: list[dict[str, object]] = []
    placeholders: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        userid = match[1]
        if userid not in placeholders:
            if len(placeholders) == 8:
                raise ValueError(
                    "WeCom supports at most eight distinct mentions per message"
                )
            placeholder = f"mention_{len(placeholders)}"
            placeholders[userid] = placeholder
            mentions.append(
                {"placeholder": placeholder, "member": {"type": 0, "userid": userid}}
            )
        return "${inline_mention:" + placeholders[userid] + "}"

    result: list[str] = []
    start = 0
    for literal in _MARKDOWN_LITERAL.finditer(body):
        result.append(pattern.sub(replace, body[start : literal.start()]))
        result.append(literal[0])
        start = literal.end()
    result.append(pattern.sub(replace, body[start:]))
    return "".join(result), mentions


def topic_message(
    address: TopicAddress,
    *,
    provider_id: str,
    body: str,
    creator: Member,
    create_time: int,
    name: str,
    attachments: tuple[InboundAttachment, ...] = (),
    parent_id: str | None = None,
    mentions_agent: bool = False,
    notifies_runtime: bool = True,
) -> Message[InboundAttachment]:
    """Project native content into the same conversation and message model as chat."""

    identity = address.provider_thread_id
    session_id = str(uuid5(NAMESPACE_URL, identity))
    metadata: dict[str, object] = {
        "sender_kind": SenderKind.HUMAN.value
        if creator.type == 0
        else SenderKind.AGENT.value,
        "review": "approved",
        "threaded": address.post_id is not None,
    }
    if parent_id is not None:
        metadata["provider_reply_to_message_id"] = parent_id
    return Message(
        direction=MessageDirection.INBOUND,
        seq=0,
        message_id=str(uuid5(NAMESPACE_URL, f"bcn:{identity}:message:{provider_id}")),
        thread_id=str(uuid5(NAMESPACE_URL, f"bcn:{identity}")),
        channel_session_id=session_id,
        target=f"group:{session_id}",
        body=body,
        sender=SenderIdentity(id=creator.id),
        target_kind=ChannelTargetKind.GROUP,
        target_presentation=ChannelTargetPresentation(display_name=name),
        channel="wecom",
        provider_thread_id=identity,
        provider_chat_id=address.topic_id,
        provider_message_id=provider_id,
        provider_time_ms=create_time * 1000,
        received_at_ms=time_ns() // 1_000_000,
        attachments=attachments,
        mentions_agent=mentions_agent,
        notifies_runtime=notifies_runtime,
        metadata=metadata,
    )


class TopicConversations:
    def __init__(
        self,
        context: ChannelContext,
        *,
        bot_id: str,
        secret: str,
        emit: Callable[[Message[InboundAttachment]], Awaitable[None]],
        logger: logging.Logger,
    ) -> None:
        self._context = context
        self._bot_id = bot_id
        self._emit = emit
        self._logger = logger
        self.client = TopicClient(bot_id, secret)
        self._events: asyncio.Queue[TopicEvent] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._names: dict[str, str] = {}
        self._roots: set[str] = set()
        self._send_lock = asyncio.Lock()
        self._stopping = True
        self._sends: set[asyncio.Task[ProviderCallResult[ChannelDeliveryReceipt]]] = (
            set()
        )

    def start(self) -> None:
        self.client.start()
        self._stopping = False
        self._worker = asyncio.create_task(self._run(), name="bcn-wecom-topics")

    async def stop(self, *, timeout: float) -> None:
        self._stopping = True
        deadline = asyncio.get_running_loop().time() + timeout
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
        try:
            if worker is not None:
                await asyncio.gather(worker, return_exceptions=True)
            if self._sends:
                await asyncio.wait(
                    tuple(self._sends),
                    timeout=max(0.0, deadline - asyncio.get_running_loop().time()),
                )
        finally:
            tasks = tuple(self._sends)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.client.close()

    def receive(self, body: Mapping[str, object]) -> bool:
        if "topic_id" not in body:
            return False
        if self._stopping:
            return True
        try:
            event = TopicEvent.model_validate(body)
        except ValidationError:
            self._logger.warning("wecom.topic.invalid_event")
            return True
        if event.aibotid != self._bot_id:
            self._logger.warning("wecom.topic.identity_mismatch")
            return True
        self._events.put_nowait(event)
        return True

    async def _run(self) -> None:
        while True:
            event = await self._events.get()
            try:
                for delay in (1, 2, 4, None):
                    try:
                        await self._receive(event)
                    except TopicApiError as error:
                        if delay is None or not error.transient:
                            raise
                        self._logger.info(
                            "wecom.topic.event_retrying: msgid=%s delay=%s %s",
                            event.msgid,
                            delay,
                            error,
                        )
                        await asyncio.sleep(delay)
                    else:
                        break
            except TopicApiError as error:
                self._logger.warning(
                    "wecom.topic.event_failed: msgid=%s %s", event.msgid, error
                )
            except Exception as error:  # noqa: BLE001
                # One failed event must not stop ingress or reveal provider credentials.
                self._logger.warning(
                    "wecom.topic.event_failed: msgid=%s %s",
                    event.msgid,
                    type(error).__name__,
                )
            finally:
                self._events.task_done()

    async def _root(self, event: TopicEvent) -> None:
        topic_id = event.topic_id
        if topic_id in self._roots:
            return
        response = await self.client.call(
            "topic/get_topic_list", {"type": 0, "topic_id_list": [topic_id]}, Topics
        )
        self._names.update(
            {topic.topic_id: topic.topic_name for topic in response.topic_list}
        )
        creator = (
            Member(type=0, userid=event.actor.userid)
            if event.actor.userid is not None
            else Member(type=1, aibotid=event.actor.aibotid)
        )
        await self._emit(
            topic_message(
                TopicAddress(self._bot_id, topic_id),
                provider_id="channel:" + event.msgid,
                body=self._names.get(topic_id, topic_id),
                creator=creator,
                create_time=event.create_time,
                name=self._names.get(topic_id, topic_id),
                notifies_runtime=False,
                mentions_agent=True,
            )
        )
        self._roots.add(topic_id)

    async def _receive(self, event: TopicEvent) -> None:
        native = event.event
        if (
            native.topic_bot_join_leave is not None
            and native.topic_bot_join_leave.change_type == 2
        ):
            self._roots.discard(event.topic_id)
            self._names.pop(event.topic_id, None)
            return
        await self._root(event)
        if native.eventtype == "topic_info_change":
            self._roots.discard(event.topic_id)
            await self._root(event)
            return
        detail = native.topic_at_bot or native.topic_reply_event or native.topic_event
        if detail is None or event.actor.aibotid == self._bot_id:
            return
        address = TopicAddress(self._bot_id, event.topic_id, detail.post_id)
        provider_id = (
            reply_message_id(detail.post_id, detail.reply_id)
            if detail.reply_id
            else post_message_id(detail.post_id)
        )
        change_type = getattr(detail, "type", 1)
        if change_type != 1:
            provider_id += ":event:" + event.msgid
        name = self._names.get(event.topic_id, event.topic_id) + " · " + detail.post_id
        if change_type == 3:
            creator = (
                Member(type=0, userid=event.actor.userid)
                if event.actor.userid is not None
                else Member(type=1, aibotid=event.actor.aibotid)
            )
            await self._emit(
                topic_message(
                    address,
                    provider_id=provider_id,
                    body="[Reply deleted]" if detail.reply_id else "[Post deleted]",
                    creator=creator,
                    create_time=event.create_time,
                    name=name,
                    notifies_runtime=False,
                )
            )
            return
        current: Post | Reply
        parent_id: str | None = None
        if detail.reply_id is None:
            current = await self.client.post(event.topic_id, detail.post_id)
        else:
            current = await self.client.reply(
                event.topic_id, detail.post_id, detail.reply_id
            )
            parent_id = (
                reply_message_id(detail.post_id, current.parent_reply_id)
                if current.parent_reply_id
                else post_message_id(detail.post_id)
            )
            if current.parent_reply_id:
                try:
                    parent = await self.client.reply(
                        event.topic_id, detail.post_id, current.parent_reply_id
                    )
                    await self._content(
                        address, parent, parent_id, name=name, quiet=True
                    )
                except TopicApiError as error:
                    if error.transient:
                        raise
                    self._logger.info(
                        "wecom.topic.reference_unavailable: msgid=%s %s",
                        event.msgid,
                        error,
                    )
        await self._content(
            address,
            current,
            provider_id,
            name=name,
            parent_id=parent_id,
            mention=native.topic_at_bot is not None,
            modified=change_type == 2,
        )

    async def _content(
        self,
        address: TopicAddress,
        content: Content,
        provider_id: str,
        *,
        name: str,
        parent_id: str | None = None,
        quiet: bool = False,
        mention: bool = False,
        modified: bool = False,
    ) -> None:
        body = content.content.replace("${inline_mention_all}", "@all")
        for item in content.inline_mention_list:
            body = body.replace(
                "${inline_mention:" + item.placeholder + "}", "@" + item.member.id + " "
            )
            mention |= item.member.type == 1 and item.member.id == self._bot_id
        attachments: list[InboundAttachment] = []
        for item in content.inline_file_list:
            body = body.replace(
                "${inline_file:" + item.placeholder + "}", f"[File: {item.file_name}]"
            )
            attachments.append(
                await self._download(item, name=item.file_name, kind="file")
            )
        for index, image in enumerate(content.image_list):
            attachments.append(
                await self._download(image, name=f"image-{index + 1}.jpg", kind="image")
            )
        if content.is_recalled:
            body = "[Content recalled]"
        elif modified:
            body = "[Content updated]\n" + body
        await self._emit(
            topic_message(
                address,
                provider_id=provider_id,
                body=body,
                creator=content.creator,
                create_time=(content.modify_time or content.create_time)
                if modified
                else content.create_time,
                name=name,
                attachments=tuple(attachments),
                parent_id=parent_id,
                mentions_agent=mention,
                notifies_runtime=not quiet and not content.is_recalled,
            )
        )

    async def _download(
        self, item: Download, *, name: str, kind: str
    ) -> InboundAttachment:
        try:
            async with self.client.session.get(
                URL(item.download_url.get_secret_value(), encoded=True),
                headers={
                    "Cookie": item.cookie_name
                    + "="
                    + item.cookie_value.get_secret_value()
                }
                if item.cookie_name
                else None,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise TopicApiError("download", f"http_{response.status}")
                media_type = response.headers.get("Content-Type")
                if kind == "image" and media_type:
                    suffix = mimetypes.guess_extension(media_type.split(";", 1)[0])
                    if suffix:
                        name = name.rsplit(".", 1)[0] + suffix
                return await self._context.attachments.materialize(
                    response.content.iter_chunked(64 * 1024),
                    name=name,
                    kind=kind,
                    media_type=media_type,
                )
        except (aiohttp.ClientError, TimeoutError, OSError, TopicApiError) as error:
            self._logger.warning(
                "wecom.topic.download_failed: %s",
                str(error)
                if isinstance(error, TopicApiError)
                else type(error).__name__,
            )
            return self._context.attachments.failed(
                name=name, kind=kind, error="WeCom attachment download failed"
            )

    async def _upload(self, attachment: PreparedAttachment) -> str:
        media_type = "image" if attachment.media_type == "image" else "file"
        result = await self.client.call(
            "upload_media_init",
            {
                "type": media_type,
                "filename": attachment.descriptor.name,
                "total_size": attachment.size_bytes,
                "total_chunks": attachment.total_chunks,
                "md5": attachment.md5,
            },
            UploadStarted,
        )
        reader = await asyncio.to_thread(AttachmentReader.open, attachment)
        try:
            for index in range(attachment.total_chunks):
                chunk = await asyncio.to_thread(reader.read_chunk)
                await self.client.call(
                    "upload_media_chunk",
                    {
                        "upload_id": result.upload_id,
                        "chunk_index": index,
                        "base64_data": base64.b64encode(chunk).decode(),
                    },
                    ApiResponse,
                )
        finally:
            await asyncio.to_thread(reader.close)
        finished = await self.client.call(
            "upload_media_finish", {"upload_id": result.upload_id}, UploadFinished
        )
        return finished.media_id

    async def send(
        self, request: ChannelSendRequest, address: TopicAddress, *, timeout: float
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        if self._stopping:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="channel_stopped",
                error_message="WeCom channel is stopped",
            )
        task = asyncio.create_task(
            self._send(request, address, timeout=timeout), name="bcn-wecom-topic-send"
        )
        self._sends.add(task)
        try:
            return await task
        finally:
            self._sends.discard(task)

    async def _send(
        self, request: ChannelSendRequest, address: TopicAddress, *, timeout: float
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        confirmed: list[str] = []
        receipt: ChannelDeliveryReceipt | None = None
        attempted = False
        try:
            async with asyncio.timeout(timeout), self._send_lock:
                if self._stopping:
                    return ProviderCallResult(
                        status=ProviderCallStatus.FAILED,
                        error_kind="channel_stopped",
                        error_message="WeCom channel is stopping",
                    )
                parent_reply_id: str | None = None
                if request.provider_reply_to_message_id is not None:
                    parts = request.provider_reply_to_message_id.removeprefix(
                        "context:"
                    ).split(":")
                    if parts[0] in {"post", "reply"}:
                        post_id = unquote(parts[1])
                        if address.post_id is not None and address.post_id != post_id:
                            raise ValueError(
                                "WeCom reply reference belongs to another post"
                            )
                        address = TopicAddress(
                            address.bot_id, address.topic_id, post_id
                        )
                        if parts[0] == "reply":
                            parent_reply_id = unquote(parts[2])
                members = (
                    await self.client.members(address.topic_id)
                    if "@" in request.body
                    else ()
                )
                # Reserve room for expanded mention placeholders and attachment markers.
                batches = (
                    list(
                        split_markdown(
                            request.body,
                            limit=_CONTENT_BYTES - 1024,
                            measure=utf8_bytes,
                        )
                    )
                    if request.body.strip()
                    else [""]
                )
                attachments = await asyncio.to_thread(
                    prepare_attachments, self._context.workspace(), request.attachments
                )
                files: list[dict[str, object]] = []
                images: list[dict[str, object]] = []
                if (
                    sum(item.media_type == "image" for item in attachments) > 8
                    or sum(item.media_type != "image" for item in attachments) > 8
                ):
                    raise ValueError(
                        "WeCom supports at most eight files and eight images per message"
                    )
                if not request.body.strip() and not attachments:
                    raise ValueError("WeCom outbound message must not be empty")
                for index, attachment in enumerate(attachments):
                    media_id = await self._upload(attachment)
                    if attachment.media_type == "image":
                        images.append({"media_id": media_id})
                    else:
                        placeholder = f"file_{index}"
                        files.append({"placeholder": placeholder, "media_id": media_id})
                        batches[-1] += "\n${inline_file:" + placeholder + "}"
                for index, batch in enumerate(batches):
                    if not batch.strip() and images:
                        batch = "\n".join(
                            attachment.descriptor.name
                            for attachment in attachments
                            if attachment.media_type == "image"
                        )
                    body, mentions = mention_content(batch, members)
                    if utf8_bytes(body) > _CONTENT_BYTES:
                        raise ValueError("WeCom expanded Markdown exceeds 20480 bytes")
                    payload: dict[str, object] = {
                        "topic_id": address.topic_id,
                        "content": body,
                    }
                    if mentions:
                        payload["inline_mention_list"] = mentions
                    if index == len(batches) - 1:
                        if files:
                            payload["inline_file_list"] = files
                        if images:
                            payload["image_list"] = images
                    attempted = True
                    if address.post_id is None:
                        result = await self.client.call(
                            "topic/send_post", payload, SentPost, visible=True
                        )
                        address = TopicAddress(
                            address.bot_id, address.topic_id, result.post_id
                        )
                        provider_id = post_message_id(result.post_id)
                    else:
                        payload["post_id"] = address.post_id
                        if parent_reply_id is not None:
                            payload["parent_reply_id"] = parent_reply_id
                        result = await self.client.call(
                            "topic/send_reply", payload, SentReply, visible=True
                        )
                        provider_id = reply_message_id(address.post_id, result.reply_id)
                    confirmed.append(provider_id)
                    receipt = ChannelDeliveryReceipt(
                        provider_message_id=provider_id,
                        provider_thread_id=address.provider_thread_id,
                    )
                    if provider_id.startswith("post:"):
                        assert address.post_id is not None
                        # Expose a newly created post through the existing group target.
                        post_body = batch
                        for index, attachment in enumerate(attachments):
                            post_body = post_body.replace(
                                "${inline_file:file_" + str(index) + "}",
                                f"[File: {attachment.descriptor.name}]",
                            )
                        await self._emit(
                            topic_message(
                                address,
                                provider_id=provider_id,
                                body=post_body,
                                creator=Member(type=1, aibotid=self._bot_id),
                                create_time=time_ns() // 1_000_000_000,
                                name=self._names.get(address.topic_id, address.topic_id)
                                + " · "
                                + address.post_id,
                                notifies_runtime=False,
                                mentions_agent=True,
                                attachments=tuple(
                                    InboundAttachment(
                                        attachment_id=str(
                                            uuid5(
                                                NAMESPACE_URL,
                                                provider_id
                                                + f":attachment:{index}:"
                                                + attachment.descriptor.sha256,
                                            )
                                        ),
                                        name=attachment.descriptor.name,
                                        kind="image"
                                        if attachment.media_type == "image"
                                        else "file",
                                        state="ready",
                                        relative_path=attachment.descriptor.relative_path,
                                        media_type=attachment.descriptor.media_type,
                                        size_bytes=attachment.size_bytes,
                                    )
                                    for index, attachment in enumerate(attachments)
                                )
                                if len(batches) == 1
                                else (),
                            )
                        )
                    attempted = False
                assert receipt is not None
                return ProviderCallResult(
                    status=ProviderCallStatus.CONFIRMED,
                    value=receipt,
                    receipt={"parts": tuple(confirmed)},
                )
        except (Exception, asyncio.CancelledError) as error:
            if isinstance(error, asyncio.CancelledError) and not self._stopping:
                raise
            unknown = (
                error.unknown
                if isinstance(error, TopicApiError)
                else isinstance(error, (TimeoutError, asyncio.CancelledError))
                and attempted
                or not isinstance(
                    error, (ValueError, OSError, TimeoutError, asyncio.CancelledError)
                )
            )
            kind = "topic_outcome_unknown" if unknown else "topic_send_failed"
            message = (
                str(error)
                if isinstance(error, (TopicApiError, ValueError))
                else type(error).__name__
            )
            if receipt is not None:
                return ProviderCallResult(
                    status=ProviderCallStatus.PARTIAL,
                    value=receipt,
                    error_kind=kind,
                    error_message=message,
                    receipt={"parts": tuple(confirmed), "unconfirmed": unknown},
                )
            return ProviderCallResult(
                status=ProviderCallStatus.UNKNOWN
                if unknown
                else ProviderCallStatus.FAILED,
                error_kind=kind,
                error_message=message,
            )
