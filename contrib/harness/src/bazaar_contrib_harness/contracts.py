"""Execution resources remain owned by each provider's contrib lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .models import (
    ApprovalRequest,
    ApprovalResult,
    HarnessDescription,
    HarnessOutput,
    HarnessSession,
    HarnessTurn,
    SessionCloseResult,
    SessionInput,
)


class IApprovalHandler(Protocol):
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResult: ...


class IHarnessTurnStream(Protocol):
    def __aiter__(self) -> AsyncIterator[HarnessOutput]: ...

    async def __anext__(self) -> HarnessOutput: ...

    async def aclose(self) -> None: ...


class IHarness(Protocol):
    async def describe(self) -> HarnessDescription: ...

    async def start_session(self, request: SessionInput) -> HarnessSession: ...

    async def start_turn(
        self,
        session: HarnessSession,
        turn: HarnessTurn,
        approval_handler: IApprovalHandler,
    ) -> IHarnessTurnStream: ...

    async def stop_session(self, session: HarnessSession) -> SessionCloseResult: ...
