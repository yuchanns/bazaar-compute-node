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
    DmAddress,
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
    SenderIdentity,
    SenderKind,
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
        self.injected_messages: list[Message] = []
        self.send_requests: list[ChannelSendRequest] = []
        self.delivered_thread_ids: dict[str, str] = {}
        self.send_attempts: list[ChannelSendRequest] = []
        self.send_gate: asyncio.Event | None = None
        self.queued_messages: list[ChannelSendRequest] = []
        self.sent_messages: list[ChannelSendRequest] = []
        self.approval_requests: list[ApprovalRequest] = []
        self.cancelled_approval_requests: list[ApprovalRequest] = []
        self.channel_approval_requests: list[ChannelApprovalRequest] = []
        self.turn_anchors: list[tuple[str, Message]] = []
        self.approval_results: list[ApprovalResult] = []
        self.events: list[RuntimeOutputEvent] = []
        self.event_sessions: list[str] = []
        self.identity: ChannelIdentity | None = None
        self.stream_events: list[RuntimeOutputEvent] = []
        self.stream_event_error: Exception | None = None
        self._inbound: asyncio.Queue[Message | object] = asyncio.Queue()
        self._send_results: deque[ProviderCallResult[ChannelDeliveryReceipt]] = deque()
        self._approval_decision = ApprovalDecision.APPROVED
        self._approval_reason: str | None = None
        self._approval_gate: asyncio.Event | None = None
        self._stop_marker = object()
        self._stop_requested = False

    def get_identity(self) -> ChannelIdentity | None:
        return self.identity if self.accepting else None

    def dm_address(
        self, sender: SenderIdentity, *, sender_kind: SenderKind
    ) -> DmAddress | None:
        if sender.id is None:
            return None
        return DmAddress(
            channel_session_id=f"channel-dm-{sender.id}",
            thread_id=f"thread-dm-{sender.id}",
            provider_thread_id=f"test:dm:{sender.id}",
            # a peer that holds a handle can be reached by it before its own id
            # can reach it
            delivery_handle=sender.name if sender_kind is SenderKind.AGENT else None,
        )

    async def start(self, *, timeout: float) -> None:
        del timeout
        self.started = True
        self.accepting = True
        self.stopped = False
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
                return
            if not isinstance(item, Message):
                raise TypeError("test channel queue contained an invalid message")
            yield item

    def anchor_turn(self, session_id: str, anchor: Message) -> None:
        self.turn_anchors.append((session_id, anchor))

    def accept_turn_event(
        self,
        item: RuntimeOutputEvent,
        *,
        session_id: str,
    ) -> None:
        self.event_sessions.append(session_id)
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
        if self.send_gate is not None:
            await self.send_gate.wait()
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
                    provider_message_id=f"test-message-{len(self.send_attempts)}",
                    # a chat opened by handle answers under the peer's own id
                    provider_thread_id=self.delivered_thread_ids.get(
                        request.provider_thread_id
                    ),
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
