"""Execution resources remain owned by each provider's contrib lifespan."""

# Provider call budgets are explicit parts of this public contract.
# ruff: noqa: ASYNC109

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from .events import HarnessOutput
from .models import (
    ApprovalRequest,
    ApprovalResult,
    HarnessExpire,
    HarnessSession,
    HarnessTurn,
    SessionInput,
)
from .outcomes import ProviderCallResult


class HarnessSessionUnavailable(RuntimeError):
    """The session failed before a provider turn request was written."""


class ApprovalHandler(Protocol):
    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult: ...


class HarnessTurnStream(Protocol):
    def __aiter__(self) -> AsyncIterator[HarnessOutput]: ...

    async def __anext__(self) -> HarnessOutput: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class HarnessSessionReconciliation:
    """Confirmed session state and an optional recovered turn stream."""

    session: HarnessSession
    stream: HarnessTurnStream | None = None


class Harness(Protocol):
    async def receive_event(self) -> HarnessExpire: ...

    async def start_session(
        self, request: SessionInput, *, timeout: float
    ) -> ProviderCallResult[HarnessSession]: ...

    async def reconcile_session(
        self,
        session: HarnessSession,
        turn: HarnessTurn | None,
        approval_handler: ApprovalHandler | None,
        *,
        timeout: float,
    ) -> ProviderCallResult[HarnessSessionReconciliation]: ...

    async def start_turn(
        self,
        session: HarnessSession,
        turn: HarnessTurn,
        approval_handler: ApprovalHandler,
        *,
        timeout: float,
    ) -> HarnessTurnStream: ...

    async def steer_turn(
        self,
        session: HarnessSession,
        turn: HarnessTurn,
        input_text: str,
        *,
        timeout: float,
    ) -> bool: ...

    async def has_background_job(
        self, session: HarnessSession, *, timeout: float
    ) -> bool: ...

    async def stop_session(
        self, session: HarnessSession, *, timeout: float
    ) -> ProviderCallResult[HarnessSession]: ...
