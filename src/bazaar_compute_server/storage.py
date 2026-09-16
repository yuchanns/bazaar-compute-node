"""What the server keeps, and the boundary any database sits behind."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .protocol import Event


@dataclass(frozen=True, slots=True)
class Computer:
    id: str
    name: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class Enrolment:
    """A new computer and the one-time token that proves it."""

    computer: Computer
    token: str


@dataclass(frozen=True, slots=True)
class StorageContext:
    """What the server hands a storage when it builds one."""

    options: Mapping[str, object]
    data_dir: Path
    retention_days: int


class IStorage(Protocol):
    """Events, computers and accounts, behind whichever database is configured."""

    @property
    def name(self) -> str: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def add_computer(self, name: str) -> Enrolment: ...

    async def list_computers(
        self, *, limit: int, after: Computer | None = None
    ) -> list[Computer]:
        """A page of computers in enrolment order, starting past `after`."""
        ...

    async def authenticate(self, token: str) -> Computer | None:
        """The computer a token speaks for, or nothing for any other token."""
        ...

    async def record_events(
        self, computer_id: str, run_id: str, events: Sequence[Event]
    ) -> int:
        """Keep the events that are new; say how many those were."""
        ...

    async def sweep(self) -> None:
        """Drop what is older than the retention, except the last health beat."""
        ...

    async def count_events(self, computer_id: str) -> int: ...


__all__ = ["Computer", "Enrolment", "IStorage", "StorageContext"]
