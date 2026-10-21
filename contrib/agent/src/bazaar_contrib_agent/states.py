"""Agent-owned provider phases and session states with their associated data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from bazaar_contrib_harness import HarnessSession

if TYPE_CHECKING:
    from .loop import Runtime


class Lifecycle(Enum):
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()


class ConnectionState(Enum):
    RUNNING = auto()
    STOPPING = auto()
    DRAINED = auto()
    STOPPED = auto()


class TurnState(Enum):
    PREPARING = auto()
    RUNNING = auto()
    FINISHED = auto()


class Validity(Enum):
    CURRENT = auto()
    EXPIRED = auto()


@dataclass(slots=True)
class SessionBinding:
    session: HarnessSession
    runtime: Runtime
    capability: str
    validity: Validity = Validity.CURRENT


@dataclass(frozen=True, slots=True)
class Dormant:
    pass


@dataclass(frozen=True, slots=True)
class Bound:
    binding: SessionBinding


@dataclass(frozen=True, slots=True)
class Idle(Bound):
    pass


@dataclass(frozen=True, slots=True)
class Working(Bound):
    pass


@dataclass(frozen=True, slots=True)
class Recovering(Bound):
    error: str


@dataclass(frozen=True, slots=True)
class Failed:
    error: str
    binding: SessionBinding | None = None


type SessionState = Dormant | Idle | Working | Recovering | Failed
