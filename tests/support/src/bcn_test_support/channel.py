from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Mapping

from bazaar_compute_node.core.channel import (
    ChannelDeliveryReceipt,
    ChannelSendRequest,
    IChannel,
)
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    InboundMessage,
    OutboundMessage,
    StreamEvent,
)
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus


class TestChannel(IChannel):
    """Controllable Channel adapter with observable inbound and outbound behavior."""

    __test__ = False

    @property
    def name(self) -> str:
        return "test"

    @property
    def health(self) -> Mapping[str, object]:
        return {
            "state": "ready" if self.accepting else "stopped",
            "full_group_ingress": True,
        }

    def __init__(self) -> None:
        self.started = False
        self.accepting = False
        self.stopped = False
        self.receive_closed = False
        self.injected_messages: list[InboundMessage] = []
        self.send_requests: list[ChannelSendRequest] = []
        self.send_attempts: list[OutboundMessage] = []
        self.queued_messages: list[OutboundMessage] = []
        self.sent_messages: list[OutboundMessage] = []
        self.approval_requests: list[ApprovalRequest] = []
        self.approval_results: list[ApprovalResult] = []
        self.stream_events: list[StreamEvent] = []
        self.stream_event_error: Exception | None = None
        self._inbound: asyncio.Queue[InboundMessage | object] = asyncio.Queue()
        self._send_results: deque[ProviderCallResult[ChannelDeliveryReceipt]] = deque()
        self._approval_results: deque[ApprovalResult] = deque()
        self._stop_marker = object()
        self._stop_requested = False

    async def start(self, *, timeout: float) -> None:
        self.started = True
        self.accepting = True
        self.stopped = False
        self.receive_closed = False
        self._stop_requested = False

    async def stop(self, *, timeout: float) -> None:
        if self._stop_requested:
            return
        self._stop_requested = True
        self.accepting = False
        self.stopped = True
        await self._inbound.put(self._stop_marker)

    async def inject(self, message: InboundMessage) -> None:
        if not self.accepting:
            raise RuntimeError("test channel is not accepting inbound messages")
        self.injected_messages.append(message)
        await self._inbound.put(message)

    async def receive(self) -> AsyncIterator[InboundMessage]:
        while True:
            item = await self._inbound.get()
            if item is self._stop_marker:
                self.receive_closed = True
                return
            if not isinstance(item, InboundMessage):
                raise TypeError("test channel queue contained an invalid message")
            yield item

    def offer_stream_event(self, event: StreamEvent) -> None:
        if self.stream_event_error is not None:
            raise self.stream_event_error
        self.stream_events.append(event)

    def queue_send_result(
        self, result: ProviderCallResult[ChannelDeliveryReceipt]
    ) -> None:
        self._send_results.append(result)

    def queue_approval_result(self, result: ApprovalResult) -> None:
        self._approval_results.append(result)

    async def send(
        self, request: ChannelSendRequest, *, timeout: float
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        self.send_requests.append(request)
        message = request.outbound
        self.send_attempts.append(message)
        if not self.started or self.stopped:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="channel_not_started",
                error_message="test channel is not started",
            )
        result = self._send_results.popleft() if self._send_results else None
        if result is None:
            result = ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=ChannelDeliveryReceipt(
                    provider_message_id=f"test-message-{message.outbound_message_id}"
                ),
            )
        if result.status is ProviderCallStatus.CONFIRMED:
            self.sent_messages.append(message)
        elif result.status is ProviderCallStatus.QUEUED:
            self.queued_messages.append(message)
        return result

    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        self.approval_requests.append(request)
        if self._approval_results:
            result = self._approval_results.popleft()
        else:
            result = ApprovalResult(
                request_id=request.request_id,
                decision=ApprovalDecision.APPROVED,
                decided_at_ms=request.created_at_ms,
            )
        self.approval_results.append(result)
        return result
