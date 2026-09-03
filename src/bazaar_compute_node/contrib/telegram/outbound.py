from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from ...core.channel import ChannelContext, ChannelDeliveryReceipt, ChannelSendRequest
from ...core.models import RuntimeOutputEvent
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from .activity import MAX_RICH_MARKDOWN_BYTES, TelegramActivityProjector
from .api import TelegramApiError, TelegramBotApi, TelegramTransportError
from .approval import TelegramApprovalChannel
from .attachments import PreparedTelegramAttachment, prepare_outbound_attachments
from .identity import TelegramThreadIdentity, parse_provider_thread_id

_MAX_RATE_LIMIT_RETRIES = 3
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True, slots=True)
class _Route:
    identity: TelegramThreadIdentity
    reply_to_message_id: int | None


@dataclass(slots=True)
class _Delivery:
    """What a multi-part Telegram send has confirmed so far."""

    total: int
    confirmed: int = 0
    receipts: list[dict[str, object]] = field(default_factory=list)

    def receipt(self) -> Mapping[str, object]:
        confirmed_ids = tuple(
            receipt["provider_message_id"]
            for receipt in self.receipts
            if receipt.get("state") == "confirmed"
            and isinstance(receipt.get("provider_message_id"), str)
        )
        return {
            "total_parts": self.total,
            "confirmed_parts": self.confirmed,
            "parts": tuple(self.receipts),
            "provider_message_id": confirmed_ids[0] if confirmed_ids else None,
            "provider_receipt_ref": confirmed_ids[-1] if confirmed_ids else None,
        }


class _DeliveryDeadlineExpired(TimeoutError):
    pass


@dataclass(frozen=True, slots=True)
class RichMarkdownPart:
    ordinal: int
    markdown: str


class TelegramOutboundChannel(TelegramApprovalChannel):
    def __init__(
        self,
        context: ChannelContext,
        *,
        token: str,
    ) -> None:
        super().__init__(context, token=token)
        self._outbound_requests = 0
        self._outbound_confirmed_requests = 0
        self._outbound_partial_requests = 0
        self._outbound_failed_requests = 0
        self._outbound_unknown_requests = 0
        self._outbound_parts_confirmed = 0
        self._outbound_documents_confirmed = 0
        self._outbound_markdown_fallbacks = 0
        self._outbound_rate_limit_retries = 0
        self._activity = TelegramActivityProjector(
            timer_wheel=self._timer_wheel,
            translator=self._translator,
        )

    @property
    def health(self) -> Mapping[str, object]:
        health = dict(super().health)
        health.update(
            {
                "outbound_requests": self._outbound_requests,
                "outbound_confirmed_requests": self._outbound_confirmed_requests,
                "outbound_partial_requests": self._outbound_partial_requests,
                "outbound_failed_requests": self._outbound_failed_requests,
                "outbound_unknown_requests": self._outbound_unknown_requests,
                "outbound_parts_confirmed": self._outbound_parts_confirmed,
                "outbound_documents_confirmed": self._outbound_documents_confirmed,
                "outbound_markdown_fallbacks": self._outbound_markdown_fallbacks,
                "outbound_rate_limit_retries": self._outbound_rate_limit_retries,
                "activity_turns": self._activity.active_turns,
                "activity_tasks_pending": self._activity.tasks_pending,
                "activity_messages_sent": self._activity.messages_sent,
                "activity_messages_edited": self._activity.messages_edited,
                "activity_failures": self._activity.failures,
                "activity_rate_limit_retries": self._activity.rate_limit_retries,
                "activity_coalesced_updates": self._activity.coalesced_updates,
            }
        )
        return health

    async def stop(self, *, timeout: float) -> None:
        await self._activity.close()
        await super().stop(timeout=timeout)

    def accept_turn_event(
        self,
        item: RuntimeOutputEvent,
        *,
        session_id: str,
    ) -> None:
        super().accept_turn_event(item, session_id=session_id)
        self._activity.accept(
            item,
            identity=self._stream_routes.get(item.envelope.session_id),
            api=self._api,
        )

    def _outbound_route(
        self, request: ChannelSendRequest, bot_id: int
    ) -> _Route | ProviderCallResult[ChannelDeliveryReceipt]:
        """Read which chat, topic and reply anchor a request names."""

        try:
            identity = parse_provider_thread_id(request.provider_thread_id)
        except ValueError as error:
            return self._failed("invalid_route", str(error))
        if identity.bot_id != bot_id:
            return self._failed(
                "invalid_route",
                "Telegram outbound route belongs to another bot",
            )
        self._stream_routes[request.session_id] = identity

        reply_to_message_id: int | None = None
        if request.provider_reply_to_message_id is not None:
            try:
                reply_to_message_id = int(request.provider_reply_to_message_id)
            except ValueError:
                return self._failed(
                    "invalid_reply_reference",
                    "Telegram reply message id must be an integer",
                )
            if reply_to_message_id <= 0:
                return self._failed(
                    "invalid_reply_reference",
                    "Telegram reply message id must be positive",
                )
        return _Route(identity=identity, reply_to_message_id=reply_to_message_id)

    async def _prepare_parts(
        self, request: ChannelSendRequest, deadline: float
    ) -> (
        tuple[tuple[RichMarkdownPart, ...], tuple[PreparedTelegramAttachment, ...]]
        | ProviderCallResult[ChannelDeliveryReceipt]
    ):
        """Split the body and stage the attachments before anything is sent."""

        loop = asyncio.get_running_loop()
        try:
            parts = split_rich_markdown(request.body) if request.body.strip() else ()
        except (TypeError, ValueError) as error:
            return self._failed("invalid_markdown", str(error))
        try:
            if request.attachments:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                attachments = await asyncio.wait_for(
                    asyncio.to_thread(
                        prepare_outbound_attachments,
                        self._context.workspace(),
                        request.attachments,
                    ),
                    timeout=remaining,
                )
            else:
                attachments = ()
        except TimeoutError:
            return self._failed(
                "delivery_timeout",
                "Telegram attachment preflight timed out",
            )
        except (OSError, TypeError, ValueError) as error:
            return self._failed("invalid_attachment", str(error))
        return parts, attachments

    async def _send_plain_blocks(
        self,
        api: TelegramBotApi,
        payload: dict[str, object],
        part: RichMarkdownPart,
        delivery: _Delivery,
        *,
        deadline: float,
    ) -> Mapping[str, object] | ProviderCallResult[ChannelDeliveryReceipt]:
        """Resend as plain blocks a part the provider refused as markdown."""

        payload["rich_message"] = plain_rich_message(part.markdown)
        try:
            provider_message = await self._send_rich_message_with_retry(
                api,
                payload,
                deadline=deadline,
            )
        except asyncio.CancelledError:
            raise
        except _DeliveryDeadlineExpired:
            return self._clear_failure(
                delivery,
                error_kind="delivery_timeout",
                error_message=(
                    "Telegram delivery deadline expired before formatting fallback"
                ),
            )
        except TelegramApiError as fallback_error:
            receipt = self._failed_part(
                ordinal=part.ordinal,
                kind="rich_message",
                delivery_format="blocks",
                error=fallback_error,
            )
            receipt["fallback_from"] = "markdown"
            delivery.receipts.append(receipt)
            return self._clear_failure(
                delivery,
                error_kind="provider_rejected_part",
                error_message="Telegram rejected an outbound Rich Message part",
            )
        except TelegramTransportError as fallback_error:
            delivery.receipts.append(
                {
                    "ordinal": part.ordinal,
                    "kind": "rich_message",
                    "format": "blocks",
                    "fallback_from": "markdown",
                    "state": "unknown",
                    "error_type": fallback_error.error_type,
                }
            )
            return self._unknown(
                delivery,
                error_kind="send_unknown",
                error_message="Telegram Rich Message delivery outcome is unknown",
            )
        return provider_message

    async def _send_text_parts(
        self,
        api: TelegramBotApi,
        delivery: _Delivery,
        parts: tuple[RichMarkdownPart, ...],
        *,
        identity: TelegramThreadIdentity,
        reply_to_message_id: int | None,
        deadline: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt] | None:
        """Send the message text, one Rich Message part at a time."""

        for part in parts:
            payload: dict[str, object] = {
                "chat_id": identity.chat_id,
                "rich_message": {"markdown": part.markdown},
            }
            if identity.topic_id:
                payload["message_thread_id"] = identity.topic_id
            if part.ordinal == 1 and reply_to_message_id is not None:
                payload["reply_parameters"] = {"message_id": reply_to_message_id}

            delivery_format = "markdown"
            fallback_from: str | None = None
            try:
                provider_message = await self._send_rich_message_with_retry(
                    api,
                    payload,
                    deadline=deadline,
                )
            except asyncio.CancelledError:
                raise
            except _DeliveryDeadlineExpired:
                return self._clear_failure(
                    delivery,
                    error_kind="delivery_timeout",
                    error_message="Telegram delivery deadline expired",
                )
            except TelegramApiError as error:
                if not is_rich_markdown_rejection(error.error_code, str(error)):
                    delivery.receipts.append(
                        self._failed_part(
                            ordinal=part.ordinal,
                            kind="rich_message",
                            delivery_format=delivery_format,
                            error=error,
                        )
                    )
                    return self._clear_failure(
                        delivery,
                        error_kind="provider_rejected_part",
                        error_message="Telegram rejected an outbound Rich Message part",
                    )

                self._outbound_markdown_fallbacks += 1
                fallback_from = "markdown"
                delivery_format = "blocks"
                fallen_back = await self._send_plain_blocks(
                    api, payload, part, delivery, deadline=deadline
                )
                if isinstance(fallen_back, ProviderCallResult):
                    return fallen_back
                provider_message = fallen_back
            except TelegramTransportError as error:
                delivery.receipts.append(
                    {
                        "ordinal": part.ordinal,
                        "kind": "rich_message",
                        "format": delivery_format,
                        "state": "unknown",
                        "error_type": error.error_type,
                    }
                )
                return self._unknown(
                    delivery,
                    error_kind="send_unknown",
                    error_message="Telegram Rich Message delivery outcome is unknown",
                )

            provider_message_id = self._outbound_provider_message_id(provider_message)
            if provider_message_id is None:
                delivery.receipts.append(
                    {
                        "ordinal": part.ordinal,
                        "kind": "rich_message",
                        "format": delivery_format,
                        "fallback_from": fallback_from,
                        "state": "unknown",
                        "error_type": "InvalidAcknowledgement",
                    }
                )
                return self._unknown(
                    delivery,
                    error_kind="invalid_send_ack",
                    error_message="Telegram send acknowledgement omitted message_id",
                )

            delivery.receipts.append(
                {
                    "ordinal": part.ordinal,
                    "kind": "rich_message",
                    "format": delivery_format,
                    "fallback_from": fallback_from,
                    "state": "confirmed",
                    "provider_message_id": provider_message_id,
                }
            )
            delivery.confirmed += 1
            self._outbound_parts_confirmed += 1
        return None

    async def _send_documents(
        self,
        api: TelegramBotApi,
        delivery: _Delivery,
        attachments: tuple[PreparedTelegramAttachment, ...],
        *,
        text_parts: int,
        identity: TelegramThreadIdentity,
        reply_to_message_id: int | None,
        deadline: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt] | None:
        """Send each attachment as its own document message."""

        for attachment_index, attachment in enumerate(attachments, start=1):
            ordinal = text_parts + attachment_index
            payload: dict[str, object] = {"chat_id": identity.chat_id}
            if identity.topic_id:
                payload["message_thread_id"] = identity.topic_id
            if (
                not text_parts
                and attachment_index == 1
                and reply_to_message_id is not None
            ):
                payload["reply_parameters"] = {"message_id": reply_to_message_id}

            receipt_base: dict[str, object] = {
                "ordinal": ordinal,
                "kind": "document",
                "name": attachment.descriptor.name,
                "media_type": attachment.media_type,
                "size_bytes": attachment.size_bytes,
            }
            try:
                provider_message = await self._send_document_with_retry(
                    api,
                    payload,
                    attachment,
                    deadline=deadline,
                )
            except asyncio.CancelledError:
                raise
            except _DeliveryDeadlineExpired:
                return self._clear_failure(
                    delivery,
                    error_kind="delivery_timeout",
                    error_message="Telegram delivery deadline expired",
                )
            except TelegramApiError as error:
                receipt = receipt_base | {
                    "state": "failed",
                    "provider_error_code": error.error_code,
                }
                if error.retry_after is not None:
                    receipt["retry_after"] = error.retry_after
                delivery.receipts.append(receipt)
                return self._clear_failure(
                    delivery,
                    error_kind="provider_rejected_part",
                    error_message="Telegram rejected an outbound document part",
                )
            except TelegramTransportError as error:
                delivery.receipts.append(
                    receipt_base | {"state": "unknown", "error_type": error.error_type}
                )
                return self._unknown(
                    delivery,
                    error_kind="send_unknown",
                    error_message="Telegram document delivery outcome is unknown",
                )
            except (OSError, TypeError, ValueError) as error:
                delivery.receipts.append(
                    receipt_base
                    | {"state": "failed", "error_type": type(error).__name__}
                )
                return self._clear_failure(
                    delivery,
                    error_kind="invalid_attachment",
                    error_message="Telegram attachment changed after preflight",
                )

            provider_message_id = self._outbound_provider_message_id(provider_message)
            if provider_message_id is None:
                delivery.receipts.append(
                    receipt_base
                    | {"state": "unknown", "error_type": "InvalidAcknowledgement"}
                )
                return self._unknown(
                    delivery,
                    error_kind="invalid_send_ack",
                    error_message="Telegram send acknowledgement omitted message_id",
                )

            delivery.receipts.append(
                receipt_base
                | {"state": "confirmed", "provider_message_id": provider_message_id}
            )
            delivery.confirmed += 1
            self._outbound_parts_confirmed += 1
            self._outbound_documents_confirmed += 1
        return None

    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        self._outbound_requests += 1
        api = self._api
        bot_id = self._bot_id
        if api is None or bot_id is None:
            return self._failed(
                "connection_unavailable",
                "Telegram channel is not ready for outbound delivery",
            )

        route = self._outbound_route(request, bot_id)
        if isinstance(route, ProviderCallResult):
            return route
        identity = route.identity
        reply_to_message_id = route.reply_to_message_id

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        prepared = await self._prepare_parts(request, deadline)
        if isinstance(prepared, ProviderCallResult):
            return prepared
        parts, attachments = prepared

        total_parts = len(parts) + len(attachments)
        if total_parts == 0:
            return self._failed(
                "empty_message",
                "Telegram outbound message must contain text or an attachment",
            )

        delivery = _Delivery(total=total_parts)

        failure = await self._send_text_parts(
            api,
            delivery,
            parts,
            identity=identity,
            reply_to_message_id=reply_to_message_id,
            deadline=deadline,
        )
        if failure is None:
            failure = await self._send_documents(
                api,
                delivery,
                attachments,
                text_parts=len(parts),
                identity=identity,
                reply_to_message_id=reply_to_message_id,
                deadline=deadline,
            )
        if failure is not None:
            return failure

        self._outbound_confirmed_requests += 1
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=self._channel_receipt(delivery.receipts),
            receipt=delivery.receipt(),
        )

    async def _send_rich_message_with_retry(
        self,
        api: TelegramBotApi,
        payload: Mapping[str, object],
        *,
        deadline: float,
    ) -> Mapping[str, object]:
        retries = 0
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise _DeliveryDeadlineExpired
            try:
                return await api.send_rich_message(payload, timeout=remaining)
            except TelegramApiError as error:
                if not await self._retry_after(
                    error,
                    deadline=deadline,
                    retries=retries,
                ):
                    raise
                retries += 1

    async def _send_document_with_retry(
        self,
        api: TelegramBotApi,
        payload: Mapping[str, object],
        attachment: PreparedTelegramAttachment,
        *,
        deadline: float,
    ) -> Mapping[str, object]:
        retries = 0
        loop = asyncio.get_running_loop()
        while True:
            if deadline - loop.time() <= 0:
                raise _DeliveryDeadlineExpired
            try:
                document = await asyncio.to_thread(attachment.open)
                try:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise _DeliveryDeadlineExpired
                    return await api.send_document(
                        payload,
                        document,
                        filename=attachment.descriptor.name,
                        media_type=attachment.media_type,
                        timeout=remaining,
                    )
                finally:
                    await asyncio.to_thread(document.close)
            except TelegramApiError as error:
                if not await self._retry_after(
                    error,
                    deadline=deadline,
                    retries=retries,
                ):
                    raise
                retries += 1

    async def _retry_after(
        self,
        error: TelegramApiError,
        *,
        deadline: float,
        retries: int,
    ) -> bool:
        retry_after = error.retry_after
        if retry_after is None or retry_after < 0 or retries >= _MAX_RATE_LIMIT_RETRIES:
            return False
        remaining = deadline - asyncio.get_running_loop().time()
        delay = float(retry_after)
        if remaining <= 0 or delay >= remaining:
            raise _DeliveryDeadlineExpired
        self._outbound_rate_limit_retries += 1
        timer_wheel = self._timer_wheel
        if timer_wheel is None:
            raise RuntimeError("Telegram rate-limit retry requires a timer wheel")
        timer = timer_wheel.create(math.ceil(delay * 1_000))
        await timer.wait()
        return True

    def _failed(
        self,
        error_kind: str,
        error_message: str,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        self._outbound_failed_requests += 1
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind=error_kind,
            error_message=error_message,
        )

    def _clear_failure(
        self,
        delivery: _Delivery,
        *,
        error_kind: str,
        error_message: str,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        receipt = delivery.receipt()
        if delivery.confirmed:
            self._outbound_partial_requests += 1
            return ProviderCallResult(
                status=ProviderCallStatus.PARTIAL,
                value=self._channel_receipt(delivery.receipts),
                error_kind=error_kind,
                error_message=error_message,
                receipt=receipt,
            )
        self._outbound_failed_requests += 1
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind=error_kind,
            error_message=error_message,
            receipt=receipt,
        )

    def _unknown(
        self,
        delivery: _Delivery,
        *,
        error_kind: str,
        error_message: str,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        self._outbound_unknown_requests += 1
        return ProviderCallResult(
            status=ProviderCallStatus.UNKNOWN,
            error_kind=error_kind,
            error_message=error_message,
            receipt=delivery.receipt(),
        )

    @staticmethod
    def _failed_part(
        *,
        ordinal: int,
        kind: str,
        delivery_format: str,
        error: TelegramApiError,
    ) -> dict[str, object]:
        receipt: dict[str, object] = {
            "ordinal": ordinal,
            "kind": kind,
            "format": delivery_format,
            "state": "failed",
            "provider_error_code": error.error_code,
        }
        if error.retry_after is not None:
            receipt["retry_after"] = error.retry_after
        return receipt

    @staticmethod
    def _outbound_provider_message_id(message: Mapping[str, object]) -> str | None:
        provider_message_id = message.get("message_id")
        if (
            not isinstance(provider_message_id, int)
            or isinstance(provider_message_id, bool)
            or provider_message_id <= 0
        ):
            return None
        return str(provider_message_id)

    @staticmethod
    def _channel_receipt(receipts: list[dict[str, object]]) -> ChannelDeliveryReceipt:
        confirmed_ids: list[str] = []
        for receipt in receipts:
            provider_message_id = receipt.get("provider_message_id")
            if receipt.get("state") == "confirmed" and isinstance(
                provider_message_id, str
            ):
                confirmed_ids.append(provider_message_id)
        if not confirmed_ids:
            raise AssertionError(
                "confirmed Telegram delivery requires provider message id"
            )
        return ChannelDeliveryReceipt(
            provider_message_id=confirmed_ids[0],
            provider_receipt_ref=(
                confirmed_ids[-1] if len(confirmed_ids) > 1 else None
            ),
        )


def split_rich_markdown(markdown: object) -> tuple[RichMarkdownPart, ...]:
    if not isinstance(markdown, str):
        raise TypeError("Telegram outbound markdown must be text")
    if not markdown.strip():
        return ()

    pieces: list[str] = []
    for block in _markdown_blocks(markdown):
        if len(block.encode("utf-8")) <= MAX_RICH_MARKDOWN_BYTES:
            pieces.append(block)
            continue
        pieces.extend(_split_oversized_block(block))

    parts: list[str] = []
    current = ""
    for piece in pieces:
        candidate = piece if not current else f"{current}\n\n{piece}"
        if len(candidate.encode("utf-8")) <= MAX_RICH_MARKDOWN_BYTES:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = piece
    if current:
        parts.append(current)

    if not parts:
        raise ValueError("Telegram outbound markdown produced no visible part")
    for part in parts:
        if not part or len(part.encode("utf-8")) > MAX_RICH_MARKDOWN_BYTES:
            raise ValueError("Telegram outbound markdown part exceeds provider limit")
    return tuple(
        RichMarkdownPart(ordinal=index, markdown=part)
        for index, part in enumerate(parts, start=1)
    )


def plain_rich_message(markdown: str) -> dict[str, object]:
    return {
        "blocks": [{"type": "paragraph", "text": markdown}],
        "skip_entity_detection": True,
    }


def is_rich_markdown_rejection(error_code: int | None, message: str) -> bool:
    if error_code != 400:
        return False
    description = message.casefold()
    return any(
        marker in description
        for marker in (
            "can't parse",
            "cannot parse",
            "parse error",
            "markdown",
            "message entity",
            "rich message entity",
        )
    )


def _markdown_blocks(markdown: str) -> tuple[str, ...]:
    blocks: list[str] = []
    lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    details_depth = 0

    for line in markdown.splitlines():
        stripped = line.strip()
        fence_match = _FENCE.match(line)
        if fence_character is None and fence_match is not None:
            marker = fence_match.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
        elif fence_character is not None and stripped.startswith(
            fence_character * fence_length
        ):
            marker = stripped.split(maxsplit=1)[0]
            if (
                marker
                and set(marker) == {fence_character}
                and len(marker) >= fence_length
            ):
                fence_character = None
                fence_length = 0

        if fence_character is None:
            folded = stripped.casefold()
            details_depth += folded.count("<details")
            details_depth -= folded.count("</details>")
            details_depth = max(0, details_depth)

        if not stripped and fence_character is None and details_depth == 0:
            if lines:
                blocks.append("\n".join(lines).strip("\n"))
                lines.clear()
            continue
        lines.append(line)

    if lines:
        blocks.append("\n".join(lines).strip("\n"))
    return tuple(block for block in blocks if block)


def _split_oversized_block(block: str) -> tuple[str, ...]:
    lines = block.splitlines()
    if len(lines) >= 3:
        opening = _FENCE.match(lines[0])
        if opening is not None:
            marker = opening.group(1)
            closing = lines[-1].strip()
            if closing and set(closing) == {marker[0]} and len(closing) >= len(marker):
                prefix = lines[0] + "\n"
                suffix = "\n" + lines[-1]
                budget = (
                    MAX_RICH_MARKDOWN_BYTES
                    - len(prefix.encode("utf-8"))
                    - len(suffix.encode("utf-8"))
                )
                if budget <= 0:
                    raise ValueError("Telegram code fence exceeds provider limit")
                content = "\n".join(lines[1:-1])
                return tuple(
                    f"{prefix}{part}{suffix}"
                    for part in _split_utf8_text(content, budget)
                )

    pieces: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate.encode("utf-8")) <= MAX_RICH_MARKDOWN_BYTES:
            current = candidate
            continue
        if current:
            pieces.append(current)
            current = ""
        if len(line.encode("utf-8")) <= MAX_RICH_MARKDOWN_BYTES:
            current = line
            continue
        pieces.extend(_split_utf8_text(line, MAX_RICH_MARKDOWN_BYTES))
    if current:
        pieces.append(current)
    return tuple(piece for piece in pieces if piece)


def _split_utf8_text(text: str, byte_limit: int) -> tuple[str, ...]:
    if byte_limit <= 0:
        raise ValueError("Telegram markdown byte limit must be positive")
    parts: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in text:
        size = len(character.encode("utf-8"))
        if size > byte_limit:
            raise ValueError("Telegram markdown contains an oversized character")
        if current and current_bytes + size > byte_limit:
            parts.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += size
    if current:
        parts.append("".join(current))
    return tuple(parts)


__all__ = [
    "RichMarkdownPart",
    "TelegramOutboundChannel",
    "is_rich_markdown_rejection",
    "plain_rich_message",
    "split_rich_markdown",
]
