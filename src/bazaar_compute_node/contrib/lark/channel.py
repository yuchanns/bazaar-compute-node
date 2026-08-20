from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from time import time_ns

import aiohttp

from ...core.channel import (
    ChannelApprovalRequest,
    ChannelContext,
    ChannelDeliveryReceipt,
    ChannelIdentity,
    ChannelSendRequest,
    IChannel,
)
from ...core.models import ApprovalDecision, ApprovalResult, InboundMessage
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.runtime import RuntimeStreamItem
from ...core.timerwheel import TimerWheel
from .api import LarkApi
from .identity import LarkBotIdentity, parse_bot_info
from .transport import LarkTransport

_STOP = object()


class LarkChannel(IChannel):
    def __init__(
        self,
        context: ChannelContext,
        *,
        app_id: str,
        app_secret: str,
        region: str,
        base_url: str,
        timer_wheel: TimerWheel,
    ) -> None:
        self._context = context
        self._app_id = app_id
        self._app_secret = app_secret
        self._region = region
        self._base_url = base_url
        self._timer_wheel = timer_wheel
        self._inbound: asyncio.Queue[InboundMessage | object] = asyncio.Queue()
        self._lifecycle_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._api: LarkApi | None = None
        self._transport: LarkTransport | None = None
        self._identity: LarkBotIdentity | None = None
        self._state = "stopped"
        self._stop_sent = False
        self._token_refresh_failures = 0
        self._connection_generation = 0

    @property
    def name(self) -> str:
        return "lark"

    @property
    def health(self) -> Mapping[str, object]:
        transport = self._transport
        transport_health = transport.health if transport is not None else {}
        state = self._state
        if transport is not None and state == "connected":
            state = str(transport_health.get("state", state))
        identity = self._identity
        return {
            "state": state,
            "region": self._region,
            "bot_open_id": identity.open_id if identity is not None else None,
            "bot_name": identity.name if identity is not None else None,
            "connection_generation": transport_health.get(
                "connection_generation", self._connection_generation
            ),
            "connected_at_ms": transport_health.get("connected_at_ms"),
            "last_event_at_ms": transport_health.get("last_event_at_ms"),
            "last_disconnect_kind": transport_health.get("last_disconnect_kind"),
            "events_received": transport_health.get("events_received", 0),
            "messages_queued": transport_health.get("messages_queued", 0),
            "messages_filtered": transport_health.get("messages_filtered", 0),
            "message_mapping_failures": transport_health.get(
                "message_mapping_failures", 0
            ),
            "last_message_disposition": transport_health.get(
                "last_message_disposition"
            ),
            "last_message_filter_reason": transport_health.get(
                "last_message_filter_reason"
            ),
            "token_refresh_failures": (
                self._api.token_refresh_failures
                if self._api is not None
                else self._token_refresh_failures
            ),
        }

    def get_identity(self) -> ChannelIdentity | None:
        identity = self._identity
        return identity.as_channel_identity() if identity is not None else None

    async def start(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise TimeoutError("Lark channel startup deadline expired")
        async with self._lifecycle_lock:
            if self._transport is not None:
                return
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            session: aiohttp.ClientSession | None = None
            api: LarkApi | None = None
            transport: LarkTransport | None = None
            self._state = "starting"
            self._stop_sent = False
            self._inbound = asyncio.Queue()
            try:
                session = aiohttp.ClientSession()
                api = LarkApi(
                    session,
                    app_id=self._app_id,
                    app_secret=self._app_secret,
                    base_url=self._base_url,
                    timer_wheel=self._timer_wheel,
                )
                await api.start()
                bot_info = await api.get_bot_info(timeout=_remaining(deadline))
                identity = parse_bot_info(bot_info)
                transport = LarkTransport(api, timer_wheel=self._timer_wheel)
                self._session = session
                self._api = api
                self._transport = transport
                await transport.start(timeout=_remaining(deadline))
                self._identity = identity
                generation = transport.health.get("connection_generation", 0)
                self._connection_generation = (
                    generation if isinstance(generation, int) else 0
                )
                self._state = "connected"
            except BaseException:
                self._state = "stopping"
                if transport is not None:
                    await transport.stop(timeout=_remaining(deadline))
                if api is not None:
                    await api.stop()
                if session is not None:
                    await session.close()
                self._session = None
                self._api = None
                self._transport = None
                self._identity = None
                self._state = "stopped"
                raise

    async def stop(self, *, timeout: float) -> None:
        if timeout < 0:
            raise ValueError("Lark channel shutdown timeout must not be negative")
        async with self._lifecycle_lock:
            transport = self._transport
            session = self._session
            api = self._api
            self._state = "stopping"
            if transport is not None:
                await transport.stop(timeout=timeout)
            if api is not None:
                self._token_refresh_failures = api.token_refresh_failures
                await api.stop()
            if session is not None:
                await session.close()
            self._transport = None
            self._api = None
            self._session = None
            self._identity = None
            self._state = "stopped"
            if not self._stop_sent:
                self._inbound.put_nowait(_STOP)
                self._stop_sent = True

    async def receive(self) -> AsyncIterator[InboundMessage]:
        inbound = self._inbound
        while True:
            item = await inbound.get()
            if item is _STOP:
                return
            if not isinstance(item, InboundMessage):
                raise TypeError("Lark inbound queue contained an invalid message")
            yield item

    def accept_turn_event(
        self,
        item: RuntimeStreamItem,
        *,
        session_id: str,
    ) -> None:
        del item, session_id

    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        del request, timeout
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="lark_delivery_unavailable",
            error_message="Lark outbound delivery is not available yet",
        )

    async def request_approval(
        self,
        request: ChannelApprovalRequest,
        *,
        timeout: float,
    ) -> ApprovalResult:
        del timeout
        return ApprovalResult(
            request_id=request.approval.request_id,
            decision=ApprovalDecision.REJECTED,
            decided_at_ms=time_ns() // 1_000_000,
            reason="lark_approval_unavailable",
        )


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())


__all__ = ["LarkChannel"]
