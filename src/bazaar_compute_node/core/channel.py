from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .approval import IApprovalHandler
from .lifecycle import IAsyncLifecycle
from .models import (
    ChannelTargetKind,
    InboundAttachment,
    InboundMessage,
    OutboundMessage,
)
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


@dataclass(frozen=True, slots=True)
class ChannelSendRequest:
    """Transient provider mapping resolved behind the runtime command boundary."""

    outbound: OutboundMessage
    target_kind: ChannelTargetKind
    provider_thread_id: str
    provider_reply_to_message_id: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_thread_id:
            raise ValueError("provider_thread_id must be non-empty")
        if (
            self.provider_reply_to_message_id is not None
            and not self.provider_reply_to_message_id
        ):
            raise ValueError("provider_reply_to_message_id must be non-empty")


class IAttachmentMaterializer(Protocol):
    async def materialize(
        self,
        source: bytes | AsyncIterable[bytes],
        *,
        name: str,
        kind: str,
        media_type: str | None = None,
    ) -> InboundAttachment:
        """Persist one bounded plaintext attachment in the shared workspace."""
        ...

    def failed(
        self,
        *,
        name: str,
        kind: str,
        error: str,
        media_type: str | None = None,
    ) -> InboundAttachment:
        """Create a terminal descriptor without exposing provider references."""
        ...


@dataclass(frozen=True, slots=True)
class ChannelContext:
    attachments: IAttachmentMaterializer
    inbound_exists: Callable[[str, str], Awaitable[bool]]
    options: Mapping[str, object]
    workspace: Callable[[], Path]


class IApproval(IApprovalHandler, Protocol):
    """Channel-owned approval policy for one bcn session."""


class IChannel(IAsyncLifecycle, IApproval, Protocol):
    """Normalized inbound, outbound delivery, and approval contract."""

    @property
    def name(self) -> str:
        """Return the stable entry-point identity of this adapter."""
        ...

    @property
    def health(self) -> Mapping[str, object]:
        """Return non-sensitive lifecycle and ingress capability details."""
        ...

    def receive(self) -> AsyncIterator[InboundMessage]:
        """Return a cancellable stream of normalized inbound messages."""
        ...

    async def send(
        self, request: ChannelSendRequest, *, timeout: float
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        """Deliver one outbound message without hiding unknown provider status."""
        ...
