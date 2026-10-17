"""A provider's connected channel, consumed through public models."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from bazaar_contrib_harness import ApprovalResult, HarnessOutput

from .models import (
    ChannelApprovalRequest,
    ChannelDeliveryReceipt,
    ChannelIdentity,
    ChannelSendRequest,
    ChannelTarget,
    InboundMessage,
)


class Channel(Protocol):
    def get_identity(self) -> ChannelIdentity: ...

    def receive(self) -> AsyncIterator[InboundMessage]: ...

    def accept_turn_event(
        self, event: HarnessOutput, *, target: ChannelTarget
    ) -> None: ...

    async def send(self, request: ChannelSendRequest) -> ChannelDeliveryReceipt: ...

    async def request_approval(
        self, request: ChannelApprovalRequest
    ) -> ApprovalResult: ...
