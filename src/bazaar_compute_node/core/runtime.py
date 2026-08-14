from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .approval import IApprovalHandler
from .client import CLIENT_INFO, ClientInfo
from .lifecycle import IAsyncLifecycle
from .models import AgentState, RuntimeEvent, RuntimeSession, RuntimeTurn, StreamEvent
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
    """Generic command capability made available to a runtime adapter."""

    run_command: Callable[[str, Sequence[str], str | None], Awaitable[None]]
    environment_for_session: Callable[[RuntimeSession], Mapping[str, str]]
    node_id: str = "bcn-node"
    runtime_options: Mapping[str, str] = field(default_factory=dict)
    sandbox_mode: RuntimeSandboxMode = RuntimeSandboxMode.WORKSPACE_WRITE
    network_access: bool = True
    startup_timeout_seconds: float = 60
    client_info: ClientInfo = CLIENT_INFO


class IRuntimeTurnStream(Protocol):
    """Cancellable stream of provider-neutral runtime events."""

    def __aiter__(self) -> AsyncIterator[RuntimeStreamItem]:
        """Iterate stream items without exposing provider wire types."""
        ...

    async def __anext__(self) -> RuntimeStreamItem:
        """Return the next stream item or raise StopAsyncIteration."""
        ...

    async def aclose(self) -> None:
        """Stop the stream and release its provider resources."""
        ...


@dataclass(frozen=True, slots=True)
class RuntimeSessionReconciliation:
    """Confirmed session state with an optional recovered turn stream."""

    session: RuntimeSession
    state: AgentState
    stream: IRuntimeTurnStream | None = None


class IRuntime(IAsyncLifecycle, Protocol):
    """Async agent-runtime contract isolated from provider SDK types.

    A stream ending before a terminal runtime event is observed is an unknown
    provider outcome. Caller cancellation propagates to the provider and is
    not converted into a confirmed failure.
    """

    @property
    def name(self) -> str:
        """Return the stable entry-point identity of this adapter."""
        ...

    def environment_variable_names(self) -> Sequence[str]:
        """Return optional daemon environment names required by this runtime."""
        ...

    async def receive_expire(self) -> RuntimeExpire:
        """Wait for the provider to report one expired runtime session."""
        ...

    async def start_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        """Start a session, releasing its process before non-confirmed return."""
        ...

    async def reconcile_session(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn | None,
        approval_handler: IApprovalHandler | None,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSessionReconciliation]:
        """Confirm recovery, releasing its process before non-confirmed return."""
        ...

    async def start_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        approval_handler: IApprovalHandler,
        *,
        timeout: float,
    ) -> IRuntimeTurnStream:
        """Start one turn and return its cancellable event stream.

        Raise RuntimeSessionUnavailable only before writing the turn request to
        the provider so orchestration can safely recover the session and retry.
        """
        ...

    async def steer_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        *,
        timeout: float,
    ) -> bool:
        """Append input to the active turn when the runtime supports steering.

        Return true only when the runtime confirms that the active turn accepted
        the input. Unsupported or unconfirmed steering returns false without
        changing the turn lifecycle.
        """
        ...

    async def interrupt_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeTurn]:
        """Request interruption without claiming provider completion."""
        ...

    async def stop_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        """Release one runtime process within the bounded shutdown budget."""
        ...
