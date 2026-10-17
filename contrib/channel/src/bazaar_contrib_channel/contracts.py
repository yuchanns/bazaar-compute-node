"""A provider's connected channel, consumed through public models."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from bazaar_contrib_harness import ApprovalResult, HarnessOutput

from .models import (
    ChannelApprovalRequest,
    ChannelIdentity,
    ChannelSendRequest,
    ChannelTarget,
    DmAddress,
    DmRequest,
    InboundMessage,
)
from .sending import ChannelSendPart


class Channel(Protocol):
    def get_identity(self) -> ChannelIdentity: ...

    def dm_address(self, request: DmRequest) -> DmAddress | None: ...

    def receive(self) -> AsyncIterator[InboundMessage]: ...

    def accept_turn_event(
        self, event: HarnessOutput, *, target: ChannelTarget
    ) -> None: ...

    async def prepare_send(
        self, request: ChannelSendRequest
    ) -> tuple[ChannelSendPart, ...]:
        """Build single-operation callbacks; upstream I/O belongs in the callbacks."""
        ...

    async def request_approval(
        self, request: ChannelApprovalRequest
    ) -> ApprovalResult: ...
