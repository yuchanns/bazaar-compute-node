from __future__ import annotations

from collections.abc import Awaitable, Callable

from bazaar_contrib_channel import (
    Channel,
    ChannelMessageDelivery,
    ChannelRetryPolicy,
    ChannelSender,
    ChannelSendRequest,
)
from bazaar_contrib_messages.delivery import from_receipt
from bazaar_contrib_messages.outcomes import OutboundDeliveryResult
from bazaar_contrib_timer import Timer


class OutboundDeliveryService:
    """Map the common channel sender's receipt into message delivery state."""

    def __init__(self, channel: Channel, timer: Timer, *, timeout: float) -> None:
        self._sender = ChannelSender(
            channel, timer, policy=ChannelRetryPolicy(timeout=timeout)
        )

    async def deliver(
        self,
        request: ChannelSendRequest,
        *,
        on_message: Callable[[ChannelMessageDelivery], Awaitable[None]],
    ) -> OutboundDeliveryResult:
        receipt = await self._sender.send(request, on_message=on_message)
        return from_receipt(receipt)
