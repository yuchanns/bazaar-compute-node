from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from ..i18n import Translator
from .lifecycle import IAsyncLifecycle
from .models import (
    ApprovalRequest,
    ApprovalResult,
    ChannelTargetKind,
    InboundAttachment,
    Message,
    OutboundAttachment,
    RuntimeOutputEvent,
)
from .outcomes import ProviderCallResult
from .timerwheel import TimerWheel


@dataclass(frozen=True, slots=True)
class ChannelIdentity:
    """Provider account identity exposed after Channel startup."""

    id: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.id is None and self.name is None:
            raise ValueError("a channel identity requires an id or name")
        for value, field_name in ((self.id, "id"), (self.name, "name")):
            if value is None:
                continue
            if not value:
                raise ValueError(f"channel identity {field_name} must be non-empty")
            if "\r" in value or "\n" in value:
                raise ValueError(
                    f"channel identity {field_name} must not contain line breaks"
                )


@dataclass(frozen=True, slots=True)
class ChannelDeliveryReceipt:
    """Provider receipt fields safe for the core delivery audit."""

    provider_message_id: str | None = None
    provider_receipt_ref: str | None = None

    def __post_init__(self) -> None:
        if self.provider_message_id is None and self.provider_receipt_ref is None:
            raise ValueError(
                "a channel delivery receipt requires a provider identifier"
            )


@dataclass(frozen=True, slots=True)
class ChannelSendRequest:
    """Transient provider mapping for one logical outbound message."""

    session_id: str
    body: str
    attachments: tuple[OutboundAttachment, ...]
    target_kind: ChannelTargetKind
    provider_thread_id: str
    provider_reply_to_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelApprovalRequest:
    """Runtime approval plus the Channel route that owns the active turn."""

    approval: ApprovalRequest
    target_kind: ChannelTargetKind
    provider_thread_id: str
    provider_reply_to_message_id: str | None = None
    provider_sender_id: str | None = None


class IAttachmentMaterializer(Protocol):
    async def materialize(
        self,
        source: bytes | AsyncIterable[bytes],
        *,
        name: str,
        kind: str,
        media_type: str | None = None,
    ) -> InboundAttachment: ...

    def failed(
        self,
        *,
        name: str,
        kind: str,
        error: str,
        media_type: str | None = None,
    ) -> InboundAttachment: ...


@dataclass(frozen=True, slots=True)
class ChannelContext:
    agent_id: str
    attachments: IAttachmentMaterializer
    options: Mapping[str, object]
    workspace: Callable[[], Path]
    translator: Translator | None = None
    timer_wheel: TimerWheel | None = None


class IApproval(Protocol):
    async def request_approval(
        self,
        request: ChannelApprovalRequest,
        *,
        timeout: float,
    ) -> ApprovalResult: ...


class IChannel(IAsyncLifecycle, IApproval, Protocol):
    @property
    def name(self) -> str: ...

    @property
    def health(self) -> Mapping[str, object]: ...

    def get_identity(self) -> ChannelIdentity | None: ...

    def receive(self) -> AsyncIterator[Message[InboundAttachment]]: ...

    def accept_turn_event(
        self,
        item: RuntimeOutputEvent,
        *,
        session_id: str,
    ) -> None:
        """Follow how a turn is going, for channels that show it."""

        return

    def anchor_turn(self, session_id: str, anchor: Message) -> None:
        """Say which message a turn's own output belongs under."""

        return

    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]: ...


class Channel(IChannel):
    """Bind a provider channel to one Agent and namespace the ids it hands out."""

    def __init__(self, agent_id: str, channel: IChannel) -> None:
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        self._agent_id = agent_id
        self._channel = channel
        self._provider_session_ids: dict[str, str] = {}

    @property
    def name(self) -> str:
        return self._channel.name

    @property
    def health(self) -> Mapping[str, object]:
        return self._channel.health

    def get_identity(self) -> ChannelIdentity | None:
        return self._channel.get_identity()

    async def start(self, *, timeout: float) -> None:
        await self._channel.start(timeout=timeout)

    async def stop(self, *, timeout: float) -> None:
        try:
            await self._channel.stop(timeout=timeout)
        finally:
            self._provider_session_ids.clear()

    async def receive(self) -> AsyncIterator[Message[InboundAttachment]]:
        async for message in self._channel.receive():
            provider_session_id = message.session_id
            channel_session_id = self._local_id(
                "channel-session",
                message.channel_session_id,
            )
            session_id = self._local_id("bcn-session", provider_session_id)
            self._provider_session_ids[session_id] = provider_session_id
            yield replace(
                message,
                session_id=session_id,
                channel_session_id=channel_session_id,
                target=f"{message.target_kind.value}:{channel_session_id}",
            )

    def accept_turn_event(
        self,
        item: RuntimeOutputEvent,
        *,
        session_id: str,
    ) -> None:
        self._channel.accept_turn_event(
            item,
            session_id=self._provider_session_ids.get(session_id, session_id),
        )

    def anchor_turn(self, session_id: str, anchor: Message) -> None:
        self._channel.anchor_turn(
            self._provider_session_ids.get(session_id, session_id), anchor
        )

    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        provider_session_id = self._provider_session_ids.get(
            request.session_id,
            request.session_id,
        )
        if provider_session_id != request.session_id:
            request = replace(request, session_id=provider_session_id)
        return await self._channel.send(request, timeout=timeout)

    async def request_approval(
        self,
        request: ChannelApprovalRequest,
        *,
        timeout: float,
    ) -> ApprovalResult:
        return await self._channel.request_approval(request, timeout=timeout)

    def _local_id(self, kind: str, provider_local_id: str) -> str:
        if not isinstance(provider_local_id, str) or not provider_local_id:
            raise ValueError(f"{kind} id must be non-empty")
        return str(
            uuid5(
                NAMESPACE_URL,
                f"bcn:{self._agent_id}:{kind}:{provider_local_id}",
            )
        )


class IChannelBuilder(Protocol):
    def build(self, context: ChannelContext) -> IChannel: ...
