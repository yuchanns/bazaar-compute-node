from __future__ import annotations

import asyncio
from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, replace
from pathlib import Path
from time import time_ns
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from ..i18n import Translator
from .audit import AuditRecorder
from .lifecycle import IAsyncLifecycle
from .models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ChannelTargetKind,
    InboundAttachment,
    Message,
    OutboundAttachment,
    RuntimeOutputEvent,
    SenderIdentity,
    SenderKind,
)
from .outcomes import ProviderCallResult, ProviderCallStatus
from .timerwheel import TimerWheel


@dataclass(frozen=True, slots=True)
class ChannelIdentity:
    """Provider account identity exposed after Channel startup."""

    id: str
    name: str | None = None

    def __post_init__(self) -> None:
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
    # the conversation the provider says this landed in, which is the only
    # authority on a chat that had to be opened by name
    provider_thread_id: str | None = None

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
    delivery_handle: str | None = None
    # the channel and bot the conversation lives on, for an agent that holds several
    channel: str | None = None
    channel_identity: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelApprovalRequest:
    """Runtime approval plus the Channel route that owns the active turn."""

    approval: ApprovalRequest
    target_kind: ChannelTargetKind
    provider_thread_id: str
    provider_reply_to_message_id: str | None = None
    provider_sender_id: str | None = None
    channel: str | None = None
    channel_identity: str | None = None


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
    audit: AuditRecorder | None = None


class IApproval(Protocol):
    async def request_approval(
        self,
        request: ChannelApprovalRequest,
        *,
        timeout: float,
    ) -> ApprovalResult: ...


@dataclass(frozen=True, slots=True)
class DmAddress:
    """A DM conversation in one channel's own terms.

    Both ids come from the channel's own identity string rather than from the
    provider thread id, so a later inbound message from the same peer lands on
    this conversation instead of creating a second one.

    `delivery_handle` is what it takes to open a chat this node has never
    spoken in, where the channel cannot reach it by id yet. Only the channel
    knows whether such a name is needed and which one counts.
    """

    channel_session_id: str
    thread_id: str
    provider_thread_id: str
    delivery_handle: str | None = None
    channel_identity: str | None = None


class IChannel(IAsyncLifecycle, IApproval, Protocol):
    @property
    def name(self) -> str: ...

    @property
    def health(self) -> Mapping[str, object]: ...

    def get_identity(self) -> ChannelIdentity | None: ...

    @property
    def members(self) -> tuple[IChannel, ...]:
        """The channels this one speaks through; itself unless it is made of several."""

        return (self,)

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

    def dm_address(
        self,
        sender: SenderIdentity,
        *,
        sender_kind: SenderKind,
        channel: str | None = None,
        channel_identity: str | None = None,
    ) -> DmAddress | None:
        """Return where a DM to this sender lives, if this channel has one.

        `channel` and `channel_identity` say which bot the sender was heard
        by, so the DM opens from that bot. `None` means the sender cannot be
        turned into a DM address here, and the caller reports the target as
        not found.
        """

        return None

    def backfill_provider_thread_id(self, provider_thread_id: str) -> str:
        """Name a conversation written before this channel could tell bots apart."""

        return provider_thread_id

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
            provider_session_id = message.thread_id
            channel_session_id = self._local_id(
                "channel-session",
                message.channel_session_id,
            )
            thread_id = self._local_id("bcn-session", provider_session_id)
            self._provider_session_ids[thread_id] = provider_session_id
            yield replace(
                message,
                thread_id=thread_id,
                channel_session_id=channel_session_id,
                channel_identity=self._identity_id(),
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

    def dm_address(
        self,
        sender: SenderIdentity,
        *,
        sender_kind: SenderKind,
        channel: str | None = None,
        channel_identity: str | None = None,
    ) -> DmAddress | None:
        del channel, channel_identity
        address = self._channel.dm_address(sender, sender_kind=sender_kind)
        if address is None:
            return None
        thread_id = self._local_id("bcn-session", address.thread_id)
        self._provider_session_ids[thread_id] = address.thread_id
        return DmAddress(
            channel_session_id=self._local_id(
                "channel-session",
                address.channel_session_id,
            ),
            thread_id=thread_id,
            provider_thread_id=address.provider_thread_id,
            delivery_handle=address.delivery_handle,
            channel_identity=self._identity_id(),
        )

    def backfill_provider_thread_id(self, provider_thread_id: str) -> str:
        return self._channel.backfill_provider_thread_id(provider_thread_id)

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

    def _identity_id(self) -> str:
        identity = self._channel.get_identity()
        if identity is None:
            raise RuntimeError(f"{self.name} channel has no identity after start")
        return identity.id

    def _local_id(self, kind: str, provider_local_id: str) -> str:
        if not isinstance(provider_local_id, str) or not provider_local_id:
            raise ValueError(f"{kind} id must be non-empty")
        return str(
            uuid5(
                NAMESPACE_URL,
                f"bcn:{self._agent_id}:{kind}:{provider_local_id}",
            )
        )


class Channels(IChannel):
    """One channel made of several, so an agent listens and answers on all of them."""

    def __init__(self, members: Sequence[IChannel]) -> None:
        if not members:
            raise ValueError("an agent needs at least one channel")
        self._members = tuple(members)
        # members that did not come up, by position, with what stopped them
        self._failures: dict[int, str] = {}
        self._bots: dict[int, tuple[str, str]] = {}
        # members whose inbound stream broke, by position, with what broke it
        self._reader_failures: dict[int, str] = {}
        # the member each live conversation came in on, for the calls that
        # only carry the conversation
        self._routes: dict[str, IChannel] = {}

    @property
    def members(self) -> tuple[IChannel, ...]:
        return self._members

    @property
    def name(self) -> str:
        return ",".join(dict.fromkeys(member.name for member in self._members))

    @property
    def health(self) -> Mapping[str, object]:
        records: list[dict[str, object]] = []
        for index, member in enumerate(self._members):
            bot = self._bots.get(index)
            record: dict[str, object] = {
                "kind": member.name,
                "identity": bot[1] if bot is not None else None,
                "health": dict(member.health),
            }
            if index in self._failures:
                record["startup_error"] = self._failures[index]
            if index in self._reader_failures:
                record["receive_error"] = self._reader_failures[index]
            records.append(record)
        return {"channels": tuple(records)}

    def get_identity(self) -> ChannelIdentity | None:
        for _, member in self._up():
            return member.get_identity()
        return None

    async def start(self, *, timeout: float) -> None:
        """Bring up every member; one that fails is marked, not fatal."""

        self._failures.clear()
        # members come up together, so the whole takes one timeout, not one
        # per member
        outcomes = await asyncio.gather(
            *(member.start(timeout=timeout) for member in self._members),
            return_exceptions=True,
        )
        for index, outcome in enumerate(outcomes):
            if isinstance(outcome, Exception):
                self._failures[index] = f"{type(outcome).__name__}: {outcome}"
        if len(self._failures) == len(self._members):
            raise RuntimeError(
                "no channel started: "
                + "; ".join(
                    f"{self._members[index].name}: {reason}"
                    for index, reason in self._failures.items()
                )
            )
        # a member knows its bot once it is up; two bots of one kind with the
        # same identity would answer each other's conversations, so that
        # pairing is refused rather than left to run
        self._bots.clear()
        seen: dict[tuple[str, str], int] = {}
        for index, member in self._up():
            identity = member.get_identity()
            if identity is None:
                await self.stop(timeout=timeout)
                raise RuntimeError(
                    f"{member.name} channel #{index + 1} has no identity after start"
                )
            bot = (member.name, identity.id)
            if bot in seen:
                await self.stop(timeout=timeout)
                raise ValueError(
                    f"channel {member.name} identity {identity.id} is configured "
                    f"twice (positions {seen[bot] + 1} and {index + 1})"
                )
            seen[bot] = index
            self._bots[index] = bot

    async def stop(self, *, timeout: float) -> None:
        # members go down together, so the whole takes one timeout, not one
        # per member
        up = list(self._up())
        outcomes = await asyncio.gather(
            *(member.stop(timeout=timeout) for _, member in up),
            return_exceptions=True,
        )
        failed = [
            f"{member.name} #{index + 1}: {type(outcome).__name__}"
            for (index, member), outcome in zip(up, outcomes, strict=True)
            if isinstance(outcome, Exception)
        ]
        if failed:
            raise RuntimeError("channel stop failed: " + "; ".join(failed))

    async def receive(self) -> AsyncIterator[Message[InboundAttachment]]:
        """Merge every member's inbound into one stream, first come first out.

        Each member is pulled one message at a time, the way a single channel
        always was, so one that stalls, floods or breaks holds back only
        itself; the stream ends when every member's has.
        """

        self._reader_failures.clear()
        streams = {index: member.receive() for index, member in self._up()}
        pending: dict[asyncio.Future[Message[InboundAttachment]], int] = {}

        def pull(index: int) -> None:
            future = asyncio.ensure_future(anext(streams[index]))
            # a break shows in health as soon as it happens, not once the
            # consumer next comes round for a message
            future.add_done_callback(lambda done: self._note_broken_stream(index, done))
            pending[future] = index

        for index in streams:
            pull(index)
        try:
            while pending:
                done, _ = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for future in done:
                    index = pending.pop(future)
                    # a member whose stream ended or broke is not pulled again
                    if future.exception() is not None:
                        continue
                    pull(index)
                    message = future.result()
                    self._routes[message.thread_id] = self._members[index]
                    yield message
        finally:
            for future in pending:
                future.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def _note_broken_stream(
        self, index: int, future: asyncio.Future[Message[InboundAttachment]]
    ) -> None:
        if future.cancelled():
            return
        error = future.exception()
        if error is None or isinstance(error, StopAsyncIteration):
            return
        self._reader_failures[index] = f"{type(error).__name__}: {error}"

    def accept_turn_event(
        self,
        item: RuntimeOutputEvent,
        *,
        session_id: str,
    ) -> None:
        member = self._owner(None, None, session_id)
        if member is not None:
            member.accept_turn_event(item, session_id=session_id)

    def anchor_turn(self, session_id: str, anchor: Message) -> None:
        member = self._owner(anchor.channel, anchor.channel_identity, session_id)
        if member is not None:
            self._routes[session_id] = member
            member.anchor_turn(session_id, anchor)

    def dm_address(
        self,
        sender: SenderIdentity,
        *,
        sender_kind: SenderKind,
        channel: str | None = None,
        channel_identity: str | None = None,
    ) -> DmAddress | None:
        if channel_identity is not None:
            member = self._owner(channel, channel_identity)
            members = () if member is None else (member,)
        else:
            members = tuple(member for _, member in self._up())
        for member in members:
            address = member.dm_address(sender, sender_kind=sender_kind)
            if address is not None:
                self._routes[address.thread_id] = member
                return address
        return None

    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        member = self._owner(
            request.channel, request.channel_identity, request.session_id
        )
        if member is None:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="channel_unavailable",
                error_message=(
                    f"{request.channel} bot {request.channel_identity} is not up"
                    if request.channel_identity is not None
                    else f"conversation {request.session_id} has no channel here"
                ),
            )
        return await member.send(request, timeout=timeout)

    async def request_approval(
        self,
        request: ChannelApprovalRequest,
        *,
        timeout: float,
    ) -> ApprovalResult:
        member = self._owner(request.channel, request.channel_identity)
        if member is None:
            return ApprovalResult(
                request_id=request.approval.request_id,
                decision=ApprovalDecision.REJECTED,
                decided_at_ms=time_ns() // 1_000_000,
                reason="channel_unavailable",
            )
        return await member.request_approval(request, timeout=timeout)

    def _owner(
        self,
        channel: str | None,
        channel_identity: str | None,
        session_id: str | None = None,
    ) -> IChannel | None:
        """The member a conversation belongs to, or None when that bot is not up.

        The bot recorded on the conversation decides, and no other member
        speaks for it. A conversation named only by its id is answered by the
        member it was last seen on here; one never seen has no owner.
        """

        if channel_identity is not None:
            for index, member in self._up():
                if self._bots[index] == (channel, channel_identity):
                    return member
            return None
        return self._routes.get(session_id) if session_id is not None else None

    def _up(self) -> Iterator[tuple[int, IChannel]]:
        for index, member in enumerate(self._members):
            if index not in self._failures:
                yield index, member


class IChannelBuilder(Protocol):
    def build(self, context: ChannelContext) -> IChannel: ...
