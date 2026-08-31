from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Mapping

from bazaar_compute_node.core.channel import (
    ChannelApprovalRequest,
    ChannelContext,
    ChannelDeliveryReceipt,
    ChannelIdentity,
    ChannelSendRequest,
    IChannel,
    IChannelBuilder,
)
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ContentDelta,
    ContextCompactionCompleted,
    ContextCompactionStarted,
    Message,
    RuntimeOutputEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallInteraction,
    ToolCallPatchUpdated,
    ToolCallStarted,
    ToolCallTextDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    TurnUnknown,
    UsageUpdated,
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
        self.injected_messages: list[Message] = []
        self.send_requests: list[ChannelSendRequest] = []
        self.send_attempts: list[ChannelSendRequest] = []
        self.queued_messages: list[ChannelSendRequest] = []
        self.sent_messages: list[ChannelSendRequest] = []
        self.approval_requests: list[ApprovalRequest] = []
        self.cancelled_approval_requests: list[ApprovalRequest] = []
        self.channel_approval_requests: list[ChannelApprovalRequest] = []
        self.approval_results: list[ApprovalResult] = []
        self.events: list[RuntimeOutputEvent] = []
        self.identity: ChannelIdentity | None = None
        self.stream_events: list[RuntimeOutputEvent] = []
        self.stream_event_error: Exception | None = None
        self._inbound: asyncio.Queue[Message | object] = asyncio.Queue()
        self._send_results: deque[ProviderCallResult[ChannelDeliveryReceipt]] = deque()
        self._approval_results: deque[ApprovalResult] = deque()
        self._approval_decision = ApprovalDecision.APPROVED
        self._approval_reason: str | None = None
        self._approval_gate: asyncio.Event | None = None
        self._stop_marker = object()
        self._stop_requested = False

    def get_identity(self) -> ChannelIdentity | None:
        return self.identity if self.accepting else None

    async def start(self, *, timeout: float) -> None:
        del timeout
        self.started = True
        self.accepting = True
        self.stopped = False
        self.receive_closed = False
        self._stop_requested = False

    async def stop(self, *, timeout: float) -> None:
        del timeout
        if self._stop_requested:
            return
        self._stop_requested = True
        self.accepting = False
        self.stopped = True
        await self._inbound.put(self._stop_marker)

    async def inject(self, message: Message) -> None:
        if not self.accepting:
            raise RuntimeError("test channel is not accepting inbound messages")
        self.injected_messages.append(message)
        await self._inbound.put(message)

    async def receive(self) -> AsyncIterator[Message]:
        while True:
            item = await self._inbound.get()
            if item is self._stop_marker:
                self.receive_closed = True
                return
            if not isinstance(item, Message):
                raise TypeError("test channel queue contained an invalid message")
            yield item

    def accept_turn_event(
        self,
        item: RuntimeOutputEvent,
        *,
        session_id: str,
    ) -> None:
        del session_id
        if self.stream_event_error is not None:
            raise self.stream_event_error
        self.events.append(item)
        match item.payload:
            case (
                TurnStarted()
                | TurnCompleted()
                | TurnFailed()
                | TurnCancelled()
                | TurnUnknown()
            ):
                pass
            case (
                ContentDelta()
                | ToolCallStarted()
                | ToolCallCompleted()
                | ToolCallFailed()
                | ToolCallTextDelta()
                | ToolCallPatchUpdated()
                | ToolCallInteraction()
                | UsageUpdated()
                | ContextCompactionStarted()
                | ContextCompactionCompleted()
            ):
                self.stream_events.append(item)

    def queue_send_result(
        self, result: ProviderCallResult[ChannelDeliveryReceipt]
    ) -> None:
        self._send_results.append(result)

    def queue_approval_result(self, result: ApprovalResult) -> None:
        self._approval_results.append(result)

    def set_approval_decision(
        self,
        decision: ApprovalDecision,
        *,
        reason: str | None = None,
    ) -> None:
        self._approval_decision = decision
        self._approval_reason = reason

    def block_approvals(self) -> None:
        self._approval_gate = asyncio.Event()

    def release_approvals(self) -> None:
        if self._approval_gate is not None:
            self._approval_gate.set()
            self._approval_gate = None

    async def send(
        self, request: ChannelSendRequest, *, timeout: float
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        del timeout
        self.send_requests.append(request)
        self.send_attempts.append(request)
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
                    provider_message_id=f"test-message-{len(self.send_attempts)}"
                ),
            )
        if result.status is ProviderCallStatus.CONFIRMED:
            self.sent_messages.append(request)
        elif result.status is ProviderCallStatus.QUEUED:
            self.queued_messages.append(request)
        return result

    async def request_approval(
        self, request: ChannelApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        del timeout
        self.channel_approval_requests.append(request)
        approval = request.approval
        self.approval_requests.append(approval)
        gate = self._approval_gate
        if gate is not None:
            try:
                await gate.wait()
            except asyncio.CancelledError:
                self.cancelled_approval_requests.append(approval)
                raise
        if self._approval_results:
            result = self._approval_results.popleft()
        else:
            result = ApprovalResult(
                request_id=approval.request_id,
                decision=self._approval_decision,
                decided_at_ms=approval.created_at_ms,
                reason=self._approval_reason,
            )
        self.approval_results.append(result)
        return result


class StaticChannelBuilder(IChannelBuilder):
    def __init__(self, channel: IChannel | None = None) -> None:
        self._channel = channel

    def build(self, context: ChannelContext) -> IChannel:
        del context
        if self._channel is not None:
            return self._channel
        return TestChannel()
