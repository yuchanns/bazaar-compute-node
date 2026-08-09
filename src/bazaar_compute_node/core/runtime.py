from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .approval import IApprovalHandler
from .client import CLIENT_INFO, ClientInfo
from .lifecycle import IAsyncLifecycle
from .models import RuntimeEvent, RuntimeSession, RuntimeTurn
from .outcomes import ProviderCallResult


@dataclass(frozen=True, slots=True)
class RuntimeCommandContext:
    """Generic command capability made available to a runtime adapter."""

    run_command: Callable[[str, Sequence[str], str | None], Awaitable[None]]
    environment_for_session: Callable[[RuntimeSession], Mapping[str, str]]
    node_id: str = "bcn-node"
    runtime_options: Mapping[str, str] = field(default_factory=dict)
    client_info: ClientInfo = CLIENT_INFO


class IRuntimeTurnStream(Protocol):
    """Cancellable stream of provider-neutral runtime events."""

    def __aiter__(self) -> AsyncIterator[RuntimeEvent]:
        """Iterate events without exposing provider wire types."""
        ...

    async def __anext__(self) -> RuntimeEvent:
        """Return the next event or raise StopAsyncIteration."""
        ...

    async def aclose(self) -> None:
        """Stop the stream and release its provider resources."""
        ...


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

    async def start_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        """Start a new runtime process/session."""
        ...

    async def resume_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        """Reconcile or resume a persisted runtime session."""
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
        """Start one turn and return its cancellable event stream."""
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
        """Stop one runtime process within the bounded shutdown budget."""
        ...
