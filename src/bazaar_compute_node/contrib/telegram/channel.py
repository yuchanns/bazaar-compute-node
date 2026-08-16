from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from time import time_ns
from uuid import NAMESPACE_URL, uuid5

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
        if bot_id is None:
            raise RuntimeError("Telegram bot identity is not initialized")
        sender = message.get("from")
        if isinstance(sender, Mapping) and sender.get("id") == bot_id:
            self._message_updates_filtered += 1
            self._last_update_disposition = "current_bot_message"
            return
        chat = message.get("chat")
        if not isinstance(chat, Mapping) or chat.get("type") != "private":
            self._message_updates_filtered += 1
            self._last_update_disposition = "unsupported_chat_type"
            return
        chat_id = chat.get("id")
        provider_message_id = message.get("message_id")
        provider_time_s = message.get("date")
        text = message.get("text")
        if (
            not isinstance(chat_id, int)
            or isinstance(chat_id, bool)
            or not isinstance(provider_message_id, int)
            or isinstance(provider_message_id, bool)
            or provider_message_id < 0
            or not isinstance(provider_time_s, int)
            or isinstance(provider_time_s, bool)
            or provider_time_s < 0
            or not isinstance(text, str)
            or not text
        ):
            self._message_updates_filtered += 1
            self._last_update_disposition = "invalid_private_text_message"
            return

        sender_id: str | None = None
        if isinstance(sender, Mapping):
            raw_sender_id = sender.get("id")
            if isinstance(raw_sender_id, int) and not isinstance(raw_sender_id, bool):
                sender_id = str(raw_sender_id)

        provider_thread_id = f"telegram:{bot_id}:{chat_id}:0"
        thread_identity = f"telegram:bot:{bot_id}:chat:{chat_id}:topic:0"
        channel_session_id = str(uuid5(NAMESPACE_URL, thread_identity))
        session_id = str(uuid5(NAMESPACE_URL, f"bcn:{thread_identity}"))
        message_id = str(
            uuid5(
                NAMESPACE_URL,
                f"telegram:bot:{bot_id}:chat:{chat_id}:message:{provider_message_id}",
            )
        )
        await self._inbound.put(
            InboundMessage(
                seq=0,
                message_id=message_id,
                session_id=session_id,
                channel_session_id=channel_session_id,
                channel="telegram",
                provider_thread_id=provider_thread_id,
                provider_message_id=str(provider_message_id),
                received_at_ms=time_ns() // 1_000_000,
                sender=sender_id,
                message_type="text",
                canonical_target=f"dm:{channel_session_id}",
                body=text,
                target_kind=ChannelTargetKind.DM,
                mentions_agent=False,
                notifies_runtime=True,
                provider_time_ms=provider_time_s * 1_000,
                metadata={
                    "telegram_update_id": update_id,
                    "telegram_chat_id": chat_id,
                    "telegram_chat_type": "private",
                },
            )
        )
        self._messages_queued += 1
        self._last_update_disposition = "message_queued"


__all__ = ["TelegramChannel"]
