from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from .approval import IApprovalHandler
from .lifecycle import IAsyncLifecycle
from .models import InboundMessage, OutboundMessage
from .outcomes import ProviderCallResult


@dataclass(frozen=True, slots=True)
class ChannelDeliveryReceipt:
    """Provider receipt fields safe for the core delivery audit."""

    provider_message_id: str | None = None
    provider_receipt_ref: str | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.provider_message_id, "provider_message_id"),
            (self.provider_receipt_ref, "provider_receipt_ref"),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(
                    f"{field_name} must be a non-empty string when present"
                )
        if self.provider_message_id is None and self.provider_receipt_ref is None:
            raise ValueError(
                "a channel delivery receipt requires a provider identifier"
            )


class IApproval(IApprovalHandler, Protocol):
    """Channel-owned approval policy for one bcn session."""


class IChannel(IAsyncLifecycle, IApproval, Protocol):
    """Normalized inbound, outbound delivery, and approval contract."""

    @property
    def name(self) -> str:
        """Return the stable entry-point identity of this adapter."""
        ...

    def receive(self) -> AsyncIterator[InboundMessage]:
        """Return a cancellable stream of normalized inbound messages."""
        ...

    async def send(
        self, message: OutboundMessage, *, timeout: float
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        """Deliver one outbound message without hiding unknown provider status."""
        ...
