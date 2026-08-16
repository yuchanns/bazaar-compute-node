from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from time import time_ns

import aiohttp

from ...core.channel import (
    ChannelApprovalRequest,
    ChannelContext,
    ChannelDeliveryReceipt,
    ChannelSendRequest,
    IChannel,
)
from ...core.models import (
    ApprovalDecision,
    ApprovalResult,
    ChannelTargetKind,
    InboundMessage,
    StreamEvent,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from .api import TelegramApiError, TelegramBotApi, TelegramTransportError
from .identity import TelegramThreadIdentity

_STOP = object()
_MAX_RECONNECT_DELAY_SECONDS = 30.0


class TelegramChannel(IChannel):
    def __init__(self, context: ChannelContext, *, token: str) -> None:
        self._context = context
        self._token = token
        self._started_at_s = time_ns() // 1_000_000_000
        self._inbound: asyncio.Queue[InboundMessage | object] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._stopping = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._session: aiohttp.ClientSession | None = None
        self._api: TelegramBotApi | None = None
        self._bot_id: int | None = None
        self._bot_username: str | None = None
        self._state = "stopped"
        self._poll_attempts = 0
        self._last_poll_at_ms: int | None = None
        self._last_poll_error_kind: str | None = None
        self._last_update_id: int | None = None
        self._last_update_at_ms: int | None = None
        self._last_update_disposition: str | None = None
        self._updates_received = 0
        self._updates_filtered = 0
        self._message_updates_received = 0
        self._message_updates_filtered = 0
        self._messages_queued = 0
        self._historical_messages_suppressed = 0
        self._activation_messages = 0
        self._quoted_messages_queued = 0
        self._callback_updates_received = 0

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def health(self) -> Mapping[str, object]:
        return {
            "state": self._state,
            "started_at_s": self._started_at_s,
            "bot_id": self._bot_id,
            "bot_username": self._bot_username,
            "poll_timeout_seconds": 50,
            "poll_attempts": self._poll_attempts,
            "last_poll_at_ms": self._last_poll_at_ms,
            "last_poll_error_kind": self._last_poll_error_kind,
            "last_update_id": self._last_update_id,
            "last_update_at_ms": self._last_update_at_ms,
            "last_update_disposition": self._last_update_disposition,
            "updates_received": self._updates_received,
            "updates_filtered": self._updates_filtered,
            "message_updates_received": self._message_updates_received,
            "message_updates_filtered": self._message_updates_filtered,
            "messages_queued": self._messages_queued,
            "historical_messages_suppressed": self._historical_messages_suppressed,
            "activation_messages": self._activation_messages,
            "quoted_messages_queued": self._quoted_messages_queued,
            "callback_updates_received": self._callback_updates_received,
        }

    async def start(self, *, timeout: float) -> None:
        if self._runner is not None:
            return
        if timeout <= 0:
            raise TimeoutError("Telegram channel startup deadline expired")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        self._state = "starting"
        self._stopping.clear()
        self._ready.clear()
        session = aiohttp.ClientSession()
        api = TelegramBotApi(session, token=self._token)
        self._session = session
        self._api = api
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("Telegram channel startup deadline expired")
            bot = await api.get_me(timeout=remaining)
            bot_id = bot.get("id")
            is_bot = bot.get("is_bot")
            username = bot.get("username")
            if (
                not isinstance(bot_id, int)
                or isinstance(bot_id, bool)
                or is_bot is not True
                or not isinstance(username, str)
                or not username
            ):
                raise ValueError("Telegram getMe returned an invalid bot identity")
            self._bot_id = bot_id
            self._bot_username = username
            self._runner = asyncio.create_task(
                self._run(),
                name="bcn-telegram-channel",
            )
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("Telegram channel startup deadline expired")
            await asyncio.wait_for(self._ready.wait(), timeout=remaining)
        except BaseException:
            self._stopping.set()
            runner = self._runner
            if runner is not None:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            self._runner = None
            self._api = None
            self._session = None
            await session.close()
            self._state = "stopped"
            raise

    async def stop(self, *, timeout: float) -> None:
        self._state = "stopping"
        self._stopping.set()
        runner = self._runner
        if runner is not None:
            runner.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(runner, return_exceptions=True),
                    timeout=max(0.0, timeout),
                )
            except TimeoutError:
                pass
        self._runner = None
        session = self._session
        self._api = None
        self._session = None
        if session is not None and not session.closed:
            await session.close()
        self._ready.clear()
        self._state = "stopped"
        await self._inbound.put(_STOP)

    async def receive(self) -> AsyncIterator[InboundMessage]:
        while True:
            item = await self._inbound.get()
            if item is _STOP:
                return
            if not isinstance(item, InboundMessage):
                raise TypeError("Telegram inbound queue contained an invalid message")
            yield item

    def offer_stream_event(self, event: StreamEvent) -> None:
        return None

    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="telegram_delivery_unavailable",
            error_message="Telegram outbound delivery is not available yet",
        )

    async def request_approval(
        self,
        request: ChannelApprovalRequest,
        *,
        timeout: float,
    ) -> ApprovalResult:
        return ApprovalResult(
            request_id=request.approval.request_id,
            decision=ApprovalDecision.REJECTED,
            decided_at_ms=time_ns() // 1_000_000,
            reason="telegram_approval_unavailable",
        )

    async def _run(self) -> None:
        api = self._api
        if api is None:
            raise RuntimeError("Telegram API client is not initialized")
        offset: int | None = None
        retry_delay = 1.0
        self._state = "ready"
        self._ready.set()
        while not self._stopping.is_set():
            self._poll_attempts += 1
            self._last_poll_at_ms = time_ns() // 1_000_000
            try:
                updates = await api.get_updates(offset=offset)
            except asyncio.CancelledError:
                raise
            except TelegramApiError as error:
                self._state = "degraded"
                self._last_poll_error_kind = (
                    f"telegram_api_{error.error_code}"
                    if error.error_code is not None
                    else "telegram_api_error"
                )
                delay = (
                    float(error.retry_after)
                    if error.retry_after is not None and error.retry_after > 0
                    else retry_delay
                )
                retry_delay = min(
                    retry_delay * 2,
                    _MAX_RECONNECT_DELAY_SECONDS,
                )
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass
                continue
            except TelegramTransportError as error:
                self._state = "degraded"
                self._last_poll_error_kind = f"telegram_transport_{error.error_type}"
                delay = retry_delay
                retry_delay = min(
                    retry_delay * 2,
                    _MAX_RECONNECT_DELAY_SECONDS,
                )
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass
                continue
            except Exception as error:  # noqa: BLE001
                self._state = "degraded"
                self._last_poll_error_kind = type(error).__name__
                delay = retry_delay
                retry_delay = min(
                    retry_delay * 2,
                    _MAX_RECONNECT_DELAY_SECONDS,
                )
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass
                continue

            self._state = "ready"
            retry_delay = 1.0
            for update in updates:
                update_id = update.get("update_id")
                if (
                    not isinstance(update_id, int)
                    or isinstance(update_id, bool)
                    or update_id < 0
                ):
                    self._updates_filtered += 1
                    self._last_update_disposition = "invalid_update_id"
                    continue
                self._updates_received += 1
                try:
                    await self._dispatch_update(update, update_id=update_id)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001
                    self._updates_filtered += 1
                    self._last_update_disposition = (
                        f"handler_error:{type(error).__name__}"
                    )
                offset = update_id + 1
                self._last_update_id = update_id
                self._last_update_at_ms = time_ns() // 1_000_000

    async def _dispatch_update(
        self,
        update: Mapping[str, object],
        *,
        update_id: int,
    ) -> None:
        message = update.get("message")
        if isinstance(message, Mapping):
            self._message_updates_received += 1
            await self._handle_message(message, update_id=update_id)
            return
        callback_query = update.get("callback_query")
        if isinstance(callback_query, Mapping):
            self._callback_updates_received += 1
            self._last_update_disposition = "callback_query"
            return
        self._updates_filtered += 1
        self._last_update_disposition = "unsupported_update"

    async def _handle_message(
        self,
        message: Mapping[str, object],
        *,
        update_id: int,
    ) -> None:
        bot_id = self._bot_id
        bot_username = self._bot_username
        if bot_id is None or bot_username is None:
            raise RuntimeError("Telegram bot identity is not initialized")

        provider_sender = message.get("from")
        if isinstance(provider_sender, Mapping) and provider_sender.get("id") == bot_id:
            self._message_updates_filtered += 1
            self._last_update_disposition = "current_bot_message"
            return

        chat = message.get("chat")
        if not isinstance(chat, Mapping):
            self._message_updates_filtered += 1
            self._last_update_disposition = "invalid_chat"
            return
        chat_type = chat.get("type")
        if chat_type not in {"private", "group", "supergroup"}:
            self._message_updates_filtered += 1
            self._last_update_disposition = "unsupported_chat_type"
            return
        chat_id = chat.get("id")
        provider_message_id = message.get("message_id")
        provider_time_s = message.get("date")
        topic_id = self._message_topic_id(message, fallback=0)
        if (
            not isinstance(chat_id, int)
            or isinstance(chat_id, bool)
            or chat_id == 0
            or not isinstance(provider_message_id, int)
            or isinstance(provider_message_id, bool)
            or provider_message_id < 0
            or not isinstance(provider_time_s, int)
            or isinstance(provider_time_s, bool)
            or provider_time_s < 0
            or topic_id is None
        ):
            self._message_updates_filtered += 1
            self._last_update_disposition = "invalid_message_identity"
            return

        body, message_type, entities = self._text_projection(message)
        if body is None:
            self._message_updates_filtered += 1
            self._last_update_disposition = "unsupported_message_content"
            return

        identity = TelegramThreadIdentity(
            bot_id=bot_id,
            chat_id=chat_id,
            topic_id=topic_id,
        )
        target_kind = (
            ChannelTargetKind.DM if chat_type == "private" else ChannelTargetKind.GROUP
        )
        sender_id, sender_is_bot = self._sender_fields(message)
        explicit_mention = self._explicitly_mentions_current_bot(
            body,
            entities,
            bot_id=bot_id,
            bot_username=bot_username,
        )
        reply = message.get("reply_to_message")
        reply_to_current_bot = False
        if isinstance(reply, Mapping):
            reply_sender = reply.get("from")
            reply_to_current_bot = (
                isinstance(reply_sender, Mapping) and reply_sender.get("id") == bot_id
            )
        activates_agent = explicit_mention or reply_to_current_bot
        historical = provider_time_s < self._started_at_s
        notifies_runtime = not historical or activates_agent
        if historical and not activates_agent:
            self._historical_messages_suppressed += 1
        if activates_agent:
            self._activation_messages += 1
        activation_reason = (
            "mention"
            if explicit_mention
            else "reply_to_bot"
            if reply_to_current_bot
            else "none"
        )

        received_at_ms = time_ns() // 1_000_000
        reply_to_message_id: str | None = None
        if isinstance(reply, Mapping):
            reply_to_message_id = await self._queue_reply_backfill(
                reply,
                update_id=update_id,
                identity=identity,
                target_kind=target_kind,
                chat_type=str(chat_type),
                received_at_ms=received_at_ms,
            )

        channel_session_id = identity.channel_session_id
        await self._inbound.put(
            InboundMessage(
                seq=0,
                message_id=identity.message_id(provider_message_id),
                session_id=identity.session_id,
                channel_session_id=channel_session_id,
                channel="telegram",
                provider_thread_id=identity.provider_thread_id,
                provider_message_id=str(provider_message_id),
                received_at_ms=received_at_ms,
                sender=sender_id,
                message_type=message_type,
                canonical_target=(
                    f"dm:{channel_session_id}"
                    if target_kind is ChannelTargetKind.DM
                    else f"group:{channel_session_id}"
                ),
                body=body,
                target_kind=target_kind,
                mentions_agent=activates_agent,
                notifies_runtime=notifies_runtime,
                provider_time_ms=provider_time_s * 1_000,
                reply_to_message_id=reply_to_message_id,
                metadata={
                    "telegram_update_id": update_id,
                    "telegram_chat_id": chat_id,
                    "telegram_message_thread_id": topic_id,
                    "telegram_chat_type": chat_type,
                    "sender_is_bot": sender_is_bot,
                    "historical": historical,
                    "activation_reason": activation_reason,
                },
            )
        )
        self._messages_queued += 1
        self._last_update_disposition = "message_queued"

    async def _queue_reply_backfill(
        self,
        reply: Mapping[str, object],
        *,
        update_id: int,
        identity: TelegramThreadIdentity,
        target_kind: ChannelTargetKind,
        chat_type: str,
        received_at_ms: int,
    ) -> str | None:
        reply_chat = reply.get("chat")
        if (
            not isinstance(reply_chat, Mapping)
            or reply_chat.get("id") != identity.chat_id
        ):
            return None
        provider_message_id = reply.get("message_id")
        if (
            not isinstance(provider_message_id, int)
            or isinstance(provider_message_id, bool)
            or provider_message_id < 0
        ):
            return None
        topic_id = self._message_topic_id(reply, fallback=identity.topic_id)
        if topic_id is None or topic_id != identity.topic_id:
            return None

        body, message_type, _entities = self._text_projection(reply)
        if body is None:
            body = ""
            message_type = "message"
        sender_id, sender_is_bot = self._sender_fields(reply)
        provider_time_s = reply.get("date")
        provider_time_ms: int | None = None
        historical: bool | None = None
        if (
            isinstance(provider_time_s, int)
            and not isinstance(provider_time_s, bool)
            and provider_time_s >= 0
        ):
            provider_time_ms = provider_time_s * 1_000
            historical = provider_time_s < self._started_at_s

        channel_session_id = identity.channel_session_id
        message_id = identity.message_id(provider_message_id)
        await self._inbound.put(
            InboundMessage(
                seq=0,
                message_id=message_id,
                session_id=identity.session_id,
                channel_session_id=channel_session_id,
                channel="telegram",
                provider_thread_id=identity.provider_thread_id,
                provider_message_id=str(provider_message_id),
                received_at_ms=received_at_ms,
                sender=sender_id,
                message_type=message_type,
                canonical_target=(
                    f"dm:{channel_session_id}"
                    if target_kind is ChannelTargetKind.DM
                    else f"group:{channel_session_id}"
                ),
                body=body,
                target_kind=target_kind,
                mentions_agent=False,
                notifies_runtime=False,
                provider_time_ms=provider_time_ms,
                metadata={
                    "telegram_update_id": update_id,
                    "telegram_chat_id": identity.chat_id,
                    "telegram_message_thread_id": identity.topic_id,
                    "telegram_chat_type": chat_type,
                    "sender_is_bot": sender_is_bot,
                    "historical": historical,
                    "activation_reason": "none",
                    "quoted_backfill": True,
                },
            )
        )
        self._messages_queued += 1
        self._quoted_messages_queued += 1
        return message_id

    @staticmethod
    def _message_topic_id(
        message: Mapping[str, object],
        *,
        fallback: int,
    ) -> int | None:
        value = message.get("message_thread_id")
        if value is None:
            return fallback
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        return value

    @staticmethod
    def _sender_fields(
        message: Mapping[str, object],
    ) -> tuple[str | None, bool | None]:
        sender = message.get("from")
        if isinstance(sender, Mapping):
            sender_id = sender.get("id")
            if isinstance(sender_id, int) and not isinstance(sender_id, bool):
                is_bot = sender.get("is_bot")
                return str(sender_id), is_bot if isinstance(is_bot, bool) else None
        sender_chat = message.get("sender_chat")
        if isinstance(sender_chat, Mapping):
            sender_chat_id = sender_chat.get("id")
            if isinstance(sender_chat_id, int) and not isinstance(sender_chat_id, bool):
                return str(sender_chat_id), None
        return None, None

    @staticmethod
    def _text_projection(
        message: Mapping[str, object],
    ) -> tuple[str | None, str, tuple[Mapping[str, object], ...]]:
        text = message.get("text")
        if isinstance(text, str) and text:
            raw_entities = message.get("entities")
            entities = (
                tuple(item for item in raw_entities if isinstance(item, Mapping))
                if isinstance(raw_entities, list)
                else ()
            )
            return text, "text", entities
        caption = message.get("caption")
        if isinstance(caption, str) and caption:
            raw_entities = message.get("caption_entities")
            entities = (
                tuple(item for item in raw_entities if isinstance(item, Mapping))
                if isinstance(raw_entities, list)
                else ()
            )
            return caption, "caption", entities
        return None, "message", ()

    @classmethod
    def _explicitly_mentions_current_bot(
        cls,
        text: str,
        entities: tuple[Mapping[str, object], ...],
        *,
        bot_id: int,
        bot_username: str,
    ) -> bool:
        username = bot_username.casefold()
        for entity in entities:
            entity_type = entity.get("type")
            if entity_type == "text_mention":
                user = entity.get("user")
                if isinstance(user, Mapping) and user.get("id") == bot_id:
                    return True
                continue
            if entity_type not in {"mention", "bot_command"}:
                continue
            entity_text = cls._utf16_entity_text(text, entity)
            if entity_text is None:
                continue
            if entity_type == "mention":
                if entity_text.casefold() == f"@{username}":
                    return True
                continue
            _command, separator, target = entity_text.rpartition("@")
            if separator and target.casefold() == username:
                return True
        return False

    @staticmethod
    def _utf16_entity_text(
        text: str,
        entity: Mapping[str, object],
    ) -> str | None:
        offset = entity.get("offset")
        length = entity.get("length")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(length, int)
            or isinstance(length, bool)
            or length <= 0
        ):
            return None
        encoded = text.encode("utf-16-le")
        start = offset * 2
        end = (offset + length) * 2
        if start > len(encoded) or end > len(encoded):
            return None
        try:
            return encoded[start:end].decode("utf-16-le")
        except UnicodeDecodeError:
            return None


__all__ = ["TelegramChannel"]
