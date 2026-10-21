"""Route one agent's public Channel operations to its connected providers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
lazy import logging

from bazaar_contrib_channel import (
    Channel,
    ChannelApprovalRequest,
    ChannelIdentity,
    ChannelSendPart,
    ChannelSendRequest,
    ChannelTarget,
    DmAddress,
    DmRequest,
    InboundMessage,
)
from bazaar_contrib_harness import ApprovalResult, HarnessOutput
from bazaar_contrib_timer import Timer

from . import states
from .models import ProviderConfig

logger = logging.getLogger(__name__)


@dataclass(eq=False, slots=True)
class Connection:
    name: str
    kind: str
    channel: Channel
    provider: ProviderConfig
    priority: int
    state: states.ConnectionState = states.ConnectionState.RUNNING


@dataclass(frozen=True, slots=True)
class Ingress:
    connection: Connection
    runtime: str | None
    message: InboundMessage


class Channels:
    def __init__(self, identity: ChannelIdentity, order: dict[str, int]):
        self.identity = identity
        self.order = order
        self.connections: dict[str, Connection] = {}
        self.turns: dict[
            tuple[str, str], dict[str, tuple[Connection, ChannelTarget]]
        ] = {}
        self.inbound: asyncio.Queue[Ingress] = asyncio.Queue()
        self.state = states.Lifecycle.RUNNING
        self.pending = 0

    def get_identity(self) -> ChannelIdentity:
        return self.identity

    async def entries(self) -> AsyncIterator[Ingress]:
        while True:
            entry = await self.inbound.get()
            try:
                yield entry
            finally:
                self.pending -= 1
                self.inbound.task_done()

    async def receive(self) -> AsyncIterator[InboundMessage]:
        async for entry in self.entries():
            yield entry.message

    def select(self, kind: str | None, identity: str | None) -> Connection:
        matches = [
            connection
            for connection in self.connections.values()
            if connection.state is not states.ConnectionState.STOPPED
            and connection.kind == kind
            and connection.channel.get_identity().id == identity
        ]
        if len(matches) != 1:
            raise ValueError(f"Channel is unavailable or ambiguous: {kind}")
        return matches[0]

    def dm_address(self, request: DmRequest) -> DmAddress | None:
        return self.select(
            request.channel, request.channel_identity
        ).channel.dm_address(request)

    async def prepare_send(
        self, request: ChannelSendRequest
    ) -> tuple[ChannelSendPart, ...]:
        connection = self.select(request.channel, request.channel_identity)
        if connection.state is states.ConnectionState.DRAINED:
            raise ValueError("Channel is stopping and no longer accepts new sends")
        return await connection.channel.prepare_send(request)

    def accept_turn_event(self, event: HarnessOutput, *, target: ChannelTarget) -> None:
        for connection, destination in self.turns[
            event.session_id, event.turn_id
        ].values():
            try:
                connection.channel.accept_turn_event(
                    event,
                    target=ChannelTarget(
                        kind=destination.kind, thread_id=destination.thread_id
                    ),
                )
            except Exception:
                logger.exception("channel rejected %s", event.kind)

    async def request_approval(
        self,
        request: ChannelApprovalRequest,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> ApprovalResult:
        for connection, target in self.turns[
            request.approval.session_id, request.approval.turn_id
        ].values():
            if target == request.target:
                return await connection.channel.request_approval(
                    request, timeout=timeout
                )
        raise ValueError("Approval target is not participating in this turn")

    async def retire(
        self, connection: Connection, timer: Timer, busy: Callable[[], Awaitable[bool]]
    ) -> None:
        if connection.state is not states.ConnectionState.DRAINED:
            connection.state = states.ConnectionState.STOPPING
        try:
            while (
                self.state is states.Lifecycle.RUNNING
                and connection.state is not states.ConnectionState.DRAINED
                and (self.pending or await busy())
            ):
                await timer.create(max(timer.tick_ms, 100)).wait()
        finally:
            connection.state = states.ConnectionState.STOPPED
            self.connections.pop(connection.name, None)
