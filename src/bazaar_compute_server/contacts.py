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
    # where a read of the conversation starts: its newest message
    latest_message_id: str | None
    # whether whoever is behind it may talk to the agent
    review: str = "approved"

    @property
    def named(self) -> tuple[str, str, str]:
        """What a link names the conversation by."""

        return self.thread_id, self.actor_id, self.target


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
    # which conversations these are: those let in, or those waiting
    review: str = "approved"
    # how many wait to be looked at, whichever are listed
    pending_review: int = 0

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
    review: str = "approved",
) -> Contacts:
    """The agent's conversations in one review state from `offset`, newest
    activity first, as far as `limit`; the computer is asked only when it is
    there to answer."""

    since = await storage.latest_message_event(agent.computer_id, agent.id)
    if agent.status == "offline":
        return Contacts(agent, (), offset, False, since, "offline", review=review)
    answer = await controls.ask(
        agent.computer_id,
        {
            "read": "contacts",
            "agent_id": agent.id,
            "limit": limit,
            "offset": offset,
            "review": review,
        },
    )
    # a page that got nothing is still there to be asked for
    if answer is None:
        return Contacts(agent, (), offset, offset > 0, since, "silent", review=review)
    if not answer.get("ok"):
        return Contacts(
            agent,
            (),
            offset,
            offset > 0,
            since,
            "refused",
            answer.get("code"),
            review=review,
        )
    result = answer["result"]
    return Contacts(
        agent,
        tuple(_contact(item) for item in result["targets"]),
        result["offset"],
        result["has_more"],
        since,
        "listed",
        review=review,
        pending_review=result["pending_review"],
    )


def contact_name(target: str) -> str:
    """The target without its scheme: `#title:id` names a group by its title,
    `dm:@handle` a peer by handle; a bare `kind:id` is all the node has."""

    if target.startswith("#"):
        return target[1:].rsplit(":", 1)[0]
    return target.split(":", 1)[1]


def _contact(item: dict[str, Any]) -> Contact:
    return Contact(
        target=item["canonical_target"],
        thread_id=item["thread_id"],
        actor_id=item["actor_id"],
        kind=item["target_kind"],
        channel=item["channel"],
        name=contact_name(item["target"]),
        latest_message_id=item["latest_message_id"],
        pending=item["pending_count"],
        last_activity_at_ms=item["last_activity_at_ms"],
        review=item["review"],
    )


__all__ = ["Contact", "Contacts", "contact_name", "contacts"]
