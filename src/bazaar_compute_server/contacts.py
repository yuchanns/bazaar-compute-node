"""An agent's conversations, as its node lists them when asked."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .control import Controls
from .fleet import PAGE_SIZE, AgentView
from .storage import IStorage


@dataclass(frozen=True, slots=True)
class Contact:
    # the conversation as the node's store names it, which is what a read is
    # asked with: the name shown may be a handle, and a handle may change hands
    target: str
    thread_id: str
    actor_id: str
    kind: str
    channel: str
    # the target without its scheme: a group's title, a peer's handle
    name: str
    pending: int
    last_activity_at_ms: int | None


@dataclass(frozen=True, slots=True)
class Contacts:
    """What the contacts column shows: the rows it got, or why it got none.
    `answer` is `listed`, or `offline` when the computer was not asked, `silent`
    when it did not answer in time, `refused` when it answered with an error."""

    agent: AgentView
    contacts: tuple[Contact, ...]
    offset: int
    more: bool
    # the newest message event known when this was read; a refresh past it is
    # only asked for when a newer one has arrived
    since: int
    answer: str
    code: str | None = None

    @property
    def edge(self) -> str | None:
        """Where the next page is asked from: past the rows, or, for a page
        that got none, where it was asked from, so it is asked again."""

        if self.contacts:
            return str(self.offset + len(self.contacts))
        return str(self.offset) if self.offset else None


async def contacts(
    storage: IStorage,
    controls: Controls,
    agent: AgentView,
    *,
    offset: int = 0,
    limit: int = PAGE_SIZE,
) -> Contacts:
    """The agent's conversations from `offset`, newest activity first, as far
    as `limit`; the computer is asked only when it is there to answer."""

    since = await storage.latest_message_event(agent.computer_id, agent.id)
    if agent.status == "offline":
        return Contacts(agent, (), offset, False, since, "offline")
    answer = await controls.ask(
        agent.computer_id,
        {"read": "contacts", "agent_id": agent.id, "limit": limit, "offset": offset},
    )
    # a page that got nothing is still there to be asked for
    if answer is None:
        return Contacts(agent, (), offset, offset > 0, since, "silent")
    if not answer.get("ok"):
        return Contacts(
            agent, (), offset, offset > 0, since, "refused", answer.get("code")
        )
    result = answer["result"]
    return Contacts(
        agent,
        tuple(_contact(item) for item in result["targets"]),
        result["offset"],
        result["has_more"],
        since,
        "listed",
    )


def _contact(item: dict[str, Any]) -> Contact:
    target: str = item["target"]
    kind: str = item["target_kind"]
    # `#title:id` names a group by its title, `dm:@handle` a peer by handle;
    # a bare `kind:id` is all the node has for it
    if target.startswith("#"):
        name = target[1:].rsplit(":", 1)[0]
    else:
        name = target.split(":", 1)[1]
    return Contact(
        target=item["canonical_target"],
        thread_id=item["thread_id"],
        actor_id=item["actor_id"],
        kind=kind,
        channel=item["channel"],
        name=name,
        pending=item["pending_count"],
        last_activity_at_ms=item["last_activity_at_ms"],
    )


__all__ = ["Contact", "Contacts", "contacts"]
