"""What the server keeps, and the boundary any database sits behind."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .protocol import Event


@dataclass(frozen=True, slots=True)
class Computer:
    id: str
    name: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class Account:
    """Someone who may log in; the hash is what a session is checked against."""

    id: str
    name: str
    password_hash: str
    created_at_ms: int
    # how this person reads the pages; none means the browser's language
    # and the system's theme
    language: str | None = None
    theme: str | None = None


@dataclass(frozen=True, slots=True)
class Enrolment:
    """A new computer and the one-time token that proves it."""

    computer: Computer
    token: str


# a conversation as events name it: on a computer, with an agent, in a thread
type ThreadKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """One event as the server keeps it."""

    id: int
    computer_id: str
    agent_id: str | None
    event_name: str
    thread_id: str | None
    created_at_ms: int
    received_at_ms: int
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ComputerHealth:
    """A computer and the last thing it said about itself."""

    computer: Computer
    health: StoredEvent | None
    last_event_at_ms: int | None


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

    async def add_account(self, name: str, password: str) -> Account:
        """A new account; the name must be unused."""
        ...

    async def find_account(self, name: str) -> Account | None: ...

    async def get_account(self, account_id: str) -> Account | None: ...

    async def verify_login(self, name: str, password: str) -> Account | None:
        """The account the credentials belong to, or nothing for any other pair."""
        ...

    async def change_password(
        self, account_id: str, password: str, *, expected_hash: str
    ) -> Account | None:
        """The account with its new password hash, or nothing when its hash
        is no longer the one the caller verified against."""
        ...

    async def set_preferences(
        self, account_id: str, *, language: str | None, theme: str | None
    ) -> Account:
        """The account with how it reads the pages from now on."""
        ...

    async def add_computer(self, name: str, *, owner_id: str) -> Enrolment:
        """A new computer, owned by the account that enrolled it."""
        ...

    async def remove_computer(self, computer_id: str) -> bool:
        """Forget a computer, everything it reported and who could see it;
        false if unknown."""
        ...

    async def find_computer(self, computer_id: str) -> Computer | None: ...

    async def list_computers(
        self,
        subject_id: str,
        *,
        after: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[Computer]:
        """The subject's computers in enrolment order: those past the id
        `after`, up to and including the id `until`, at most `limit` of them."""
        ...

    async def has_relation(self, subject_id: str, kind: str, target_id: str) -> bool:
        """Whether the subject stands in that relation to the target."""
        ...

    async def related(
        self, subject_id: str, kind: str, target_ids: Sequence[str]
    ) -> set[str]:
        """Which of the targets the subject stands in that relation to."""
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

    async def latest_message_event(
        self, computer_id: str, agent_id: str, *, thread_id: str | None = None
    ) -> int:
        """The id of the newest event that put a message into or out of the
        agent's conversations, or one of them; 0 when there is none yet."""
        ...

    async def computer_health(
        self, computers: Sequence[Computer]
    ) -> list[ComputerHealth]:
        """The computers with their latest health beat and last event time."""
        ...

    async def recent_activity(
        self, computer_id: str, agent_id: str, *, limit: int
    ) -> list[StoredEvent]:
        """An agent's latest events, newest first, health beats left out."""
        ...

    async def latest_per_agent(
        self, computer_ids: Sequence[str], names: Sequence[str]
    ) -> list[StoredEvent]:
        """For every thread of every agent on the computers, its newest event
        with one of the names; threads with none are simply absent."""
        ...

    async def thread_names(self, threads: Sequence[ThreadKey]) -> dict[ThreadKey, str]:
        """What each thread is called, from the newest message seen in it;
        threads no message named are absent."""
        ...

    async def usage_around(
        self, computer_id: str, agent_id: str, at_ms: int
    ) -> list[StoredEvent]:
        """Per runtime session, the latest `usage.updated` before a moment and
        the latest one from that moment on."""
        ...


__all__ = [
    "Account",
    "Computer",
    "ComputerHealth",
    "Enrolment",
    "IStorage",
    "StorageContext",
    "StoredEvent",
    "ThreadKey",
]
