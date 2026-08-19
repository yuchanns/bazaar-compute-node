from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .approval import IApprovalHandler
from .client import CLIENT_INFO, ClientInfo
from .lifecycle import IAsyncLifecycle
from .models import (
    RuntimeEvent,
    RuntimeSession,
    RuntimeTurn,
    SessionRuntimeState,
    StreamEvent,
)
from .outcomes import ProviderCallResult


class RuntimeSessionUnavailable(RuntimeError):
    """The runtime session failed before a provider turn request was written."""


class RuntimeSandboxMode(StrEnum):
    """Provider-neutral filesystem sandbox modes for runtime turns."""

    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


type RuntimeStreamItem = RuntimeEvent | StreamEvent


@dataclass(frozen=True, slots=True)
class RuntimeExpire:
    """Request expiry of one live runtime session."""

    runtime_session_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_session_id, str) or not self.runtime_session_id:
            raise ValueError("runtime_session_id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class RuntimeCommandContext:
    """Generic command capability made available to one Agent runtime adapter."""

    run_command: Callable[[str, Sequence[str], str | None], Awaitable[None]]
    environment_for_session: Callable[[RuntimeSession], Mapping[str, str]]
    agent_id: str
    agent_name: str
    bot_name: Callable[[], str | None]
    runtime_options: Mapping[str, str] = field(default_factory=dict)
    sandbox_mode: RuntimeSandboxMode = RuntimeSandboxMode.WORKSPACE_WRITE
    network_access: bool = True
    startup_timeout_seconds: float = 60
    client_info: ClientInfo = CLIENT_INFO

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not self.agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if "\r" in self.agent_id or "\n" in self.agent_id:
            raise ValueError("agent_id must not contain line breaks")
        if not isinstance(self.agent_name, str) or not self.agent_name:
            raise ValueError("agent_name must be a non-empty string")
        if "\r" in self.agent_name or "\n" in self.agent_name:
            raise ValueError("agent_name must not contain line breaks")
        if not callable(self.bot_name):
            raise TypeError("bot_name must be callable")


class IRuntimeTurnStream(Protocol):
    """Cancellable stream of provider-neutral runtime events."""

    def __aiter__(self) -> AsyncIterator[RuntimeStreamItem]: ...

    async def __anext__(self) -> RuntimeStreamItem: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeSessionReconciliation:
    """Confirmed session runtime state with an optional recovered turn stream."""

    session: RuntimeSession
    state: SessionRuntimeState
    stream: IRuntimeTurnStream | None = None


class IRuntime(IAsyncLifecycle, Protocol):
    """Async runtime contract isolated from provider SDK types."""

    @property
    def name(self) -> str: ...

    def environment_variable_names(self) -> Sequence[str]: ...

    async def receive_expire(self) -> RuntimeExpire: ...

    async def start_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]: ...

    async def reconcile_session(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn | None,
        approval_handler: IApprovalHandler | None,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSessionReconciliation]: ...

    async def start_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        approval_handler: IApprovalHandler,
        *,
        timeout: float,
    ) -> IRuntimeTurnStream: ...

    async def steer_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        *,
        timeout: float,
    ) -> bool: ...

    async def interrupt_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeTurn]: ...

    async def has_background_job(
        self, session: RuntimeSession, *, timeout: float
    ) -> bool: ...

    async def stop_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]: ...
