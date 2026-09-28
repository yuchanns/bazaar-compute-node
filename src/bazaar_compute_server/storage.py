"""What the server keeps, and the boundary any database sits behind."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .permissions import Permission
from .protocol import Event


@dataclass(frozen=True, slots=True)
class Computer:
    id: str
    name: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class Account:
    """Someone who may log in, with identity type and session version."""

    id: str
    name: str
    password_hash: str
    created_at_ms: int
    # how this person reads the pages; none means the browser's language
    # and the system's theme
    language: str | None = None
    theme: str | None = None
    auth_type: str = "local"
    session_version: int = 0
    display_name: str = ""
    provider_name: str = ""


@dataclass(frozen=True, slots=True)
class Role:
    id: str
    name: str
    permissions: frozenset[Permission]
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class OIDCProvider:
    id: str
    name: str
    logo_url: str
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    created_at_ms: int
    updated_at_ms: int
    description: str = ""
    default_role_id: str | None = None


@dataclass(frozen=True, slots=True)
class OIDCTransaction:
    state: str
    provider_id: str
    browser_hash: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    expires_at_ms: int
    issuer: str = ""
    subject: str = ""


@dataclass(frozen=True, slots=True)
class RoleShare:
    role_id: str
    kind: str
    target_id: str
    permissions: frozenset[Permission]


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

    async def list_accounts(self, role_id: str | None = None) -> list[Account]: ...

    async def list_roles(self, account_id: str | None = None) -> list[Role]: ...

    async def save_role(self, role: Role) -> None: ...

    async def remove_role(
        self, role_id: str, *, replacement_id: str | None = None
    ) -> None: ...

    async def set_account_roles(
        self, account_id: str, role_ids: Sequence[str]
    ) -> None: ...

    async def default_role(self) -> Role | None: ...

    async def set_default_role(self, role_id: str) -> None: ...

    async def login_order(self) -> list[str]: ...

    async def save_login_order(self, order: list[str]) -> None: ...

    async def list_oidc_providers(self) -> list[OIDCProvider]: ...

    async def get_oidc_provider(self, provider_id: str) -> OIDCProvider | None: ...

    async def save_oidc_provider(self, provider: OIDCProvider) -> None: ...

    async def oidc_account(
        self, provider_id: str, issuer: str, subject: str, display_name: str, email: str
    ) -> Account: ...

    async def save_oidc_transaction(self, transaction: OIDCTransaction) -> None: ...

    async def consume_oidc_transaction(
        self, state: str, browser_hash: str, provider_id: str
    ) -> OIDCTransaction | None: ...

    async def list_role_shares(self, kind: str, target_id: str) -> list[RoleShare]: ...

    async def save_role_share(self, share: RoleShare) -> None: ...

    async def remove_role_share(
        self, role_id: str, kind: str, target_id: str
    ) -> None: ...

    async def agent_computer(self, agent_id: str) -> str | None: ...

    async def allowed_targets(
        self, account_id: str, kind: str, point: Permission
    ) -> set[str]: ...

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
        for_agents: bool = False,
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

    async def latest_activity_event(
        self, computer_id: str, agent_id: str, *, skipping: Sequence[str]
    ) -> int: ...

    async def latest_named_event(
        self,
        computer_id: str,
        agent_id: str,
        *,
        names: Sequence[str],
        thread_id: str | None = None,
    ) -> int: ...

    async def computer_health(
        self, computers: Sequence[Computer]
    ) -> list[ComputerHealth]:
        """The computers with their latest health beat and last event time."""
        ...

    async def recent_activity(
        self,
        computer_id: str,
        agent_id: str,
        *,
        limit: int,
        skipping: Sequence[str],
        before: int | None = None,
        after: int | None = None,
    ) -> list[StoredEvent]:
        """An agent's events, newest first, those named left out: the latest,
        or those older than the event id `before`; with `after`, the first
        ones newer than it - the page right after the cursor, not the
        newest, so none between is passed over."""
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

    async def shorten(self, values: Sequence[str]) -> list[int]:
        """A short number for each value, given once and kept for good; one
        not seen before gets its number here. In the order given."""
        ...

    async def expand(self, ids: Sequence[int]) -> list[str | None]:
        """The value behind each number, nothing where there is none. In the
        order given."""
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
