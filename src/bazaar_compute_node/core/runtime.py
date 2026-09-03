from __future__ import annotations

from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Container,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .approval import IApprovalHandler
from .client import CLIENT_INFO, ClientInfo
from .lifecycle import IAsyncLifecycle
from .models import (
    RuntimeOutputEvent,
    RuntimeSession,
    RuntimeTurn,
)
from .outcomes import ProviderCallResult


class RuntimeSessionUnavailable(RuntimeError):
    """The runtime session failed before a provider turn request was written."""


class RuntimeSandboxMode(StrEnum):
    """Provider-neutral filesystem sandbox modes for runtime turns."""

    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


@dataclass(frozen=True, slots=True)
class RuntimeExpire:
    """Request expiry of one live runtime session."""

    runtime_session_id: str


@dataclass(frozen=True, slots=True)
class RuntimeBackgroundIdle:
    """Report that one runtime session's background work became idle."""

    runtime_session_id: str


type RuntimeLifecycleEvent = RuntimeExpire | RuntimeBackgroundIdle


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
        if "\r" in self.agent_id or "\n" in self.agent_id:
            raise ValueError("agent_id must not contain line breaks")
        if "\r" in self.agent_name or "\n" in self.agent_name:
            raise ValueError("agent_name must not contain line breaks")


class IRuntimeTurnStream(Protocol):
    """Cancellable stream of provider-neutral runtime events."""

    def __aiter__(self) -> AsyncIterator[RuntimeOutputEvent]: ...

    async def __anext__(self) -> RuntimeOutputEvent: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeSessionReconciliation:
    """Confirmed session runtime state with an optional recovered turn stream."""

    session: RuntimeSession
    stream: IRuntimeTurnStream | None = None


class IRuntime(IAsyncLifecycle, Protocol):
    """Async runtime contract isolated from provider SDK types."""

    @property
    def name(self) -> str: ...

    def environment_variable_names(self) -> Sequence[str]: ...

    async def receive_event(self) -> RuntimeLifecycleEvent: ...

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

    async def has_background_job(
        self, session: RuntimeSession, *, timeout: float
    ) -> bool: ...

    async def stop_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]: ...


_BAN_MS = 3_600_000


class Runtime:
    """Hold one Agent's runtimes and remember which one holds a conversation.

    Configuration order is priority order, so a runtime keeps serving until it
    fails. A session stays on the runtime that holds its conversation, which is
    what steering and later turns have to reach, until that runtime is banned.
    """

    def __init__(
        self,
        runtimes: Sequence[IRuntime],
        *,
        clock: Callable[[], int],
    ) -> None:
        if not runtimes:
            raise ValueError("runtimes must not be empty")
        for runtime in runtimes:
            if not isinstance(runtime.name, str) or not runtime.name:
                raise ValueError("runtime.name must be a non-empty string")
        self._runtimes = tuple(runtimes)
        self._clock = clock
        self._ban_until_ms: dict[int, int] = {}
        self._holders: dict[str, int] = {}

    def all(self) -> tuple[IRuntime, ...]:
        return self._runtimes

    def get(self, index: int) -> IRuntime:
        return self._runtimes[index]

    def select(self, *, exclude: Container[int] = ()) -> int:
        # configuration order is priority order: a runtime keeps serving until
        # it fails, and only its ban moves selection on to the next one.
        # exclude carries the runtimes one turn has already tried
        now_ms = self._clock()
        candidates = [
            index for index in range(len(self._runtimes)) if index not in exclude
        ]
        if not candidates:
            raise ValueError("every runtime is excluded")
        for index in candidates:
            ban_until_ms = self._ban_until_ms.get(index)
            if ban_until_ms is None or ban_until_ms <= now_ms:
                self._ban_until_ms.pop(index, None)
                return index
        # everything is banned, so half-open the one banned longest ago and
        # leave its ban standing: a failed probe extends it and moves the slot
        # on to the next runtime, a completed turn lifts it
        return min(candidates, key=self._ban_until_ms.__getitem__)

    def record_failure(self, index: int) -> int:
        ban_until_ms = self._clock() + _BAN_MS
        self._ban_until_ms[index] = ban_until_ms
        return ban_until_ms

    def record_success(self, index: int) -> int | None:
        return self._ban_until_ms.pop(index, None)

    def holder(self, session_id: str) -> int | None:
        """Return the runtime that currently holds one session's conversation."""

        return self._holders.get(session_id)

    def bind(self, session_id: str, index: int) -> None:
        self._holders[session_id] = index

    def release(self, session_id: str) -> None:
        self._holders.pop(session_id, None)

    def release_all(self) -> None:
        self._holders.clear()
