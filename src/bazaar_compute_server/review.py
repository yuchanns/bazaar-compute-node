"""Who may talk to an agent, and what it is set to say: what an operator
decides from the server, carried down to the agent's node."""

from __future__ import annotations

from dataclasses import dataclass

from .control import Controls
from .fleet import AgentView
from .history import Contact

# the one setting the pages know: what a conversation still waiting is told
REVIEW_REPLY = "review.reply"


@dataclass(frozen=True, slots=True)
class Request:
    """A conversation waiting to be looked at, with the first thing said in
    it; or why the node could not say."""

    agent: AgentView
    contact: Contact
    sender: str | None
    at_ms: int | None
    body: str | None
    answer: str
    code: str | None = None


@dataclass(frozen=True, slots=True)
class Reminder:
    title: str
    next_fire_at_ms: int | None
    repeat_rule: str | None


@dataclass(frozen=True, slots=True)
class Reminders:
    items: tuple[Reminder, ...]
    answer: str
    code: str | None = None


async def request(controls: Controls, agent: AgentView, contact: Contact) -> Request:
    """The first message of a conversation waiting to be looked at."""

    answer = await controls.outcome(
        agent.computer_id,
        {
            "read": "history",
            "agent_id": agent.id,
            "actor_id": contact.actor_id,
            "target": contact.target,
            "limit": 1,
        },
        online=agent.status != "offline",
    )
    if isinstance(answer, str):
        word, _, code = answer.partition(":")
        return Request(agent, contact, None, None, None, word, code or None)
    first = next(iter(answer["messages"]), None)
    if first is None:
        return Request(agent, contact, None, None, None, "listed")
    sender = first["sender"] or {}
    return Request(
        agent,
        contact,
        sender.get("display_name") or sender.get("name") or sender.get("id"),
        first["provider_time_ms"] or first["received_at_ms"] or first["created_at_ms"],
        first["body"],
        "listed",
    )


async def decide(
    controls: Controls, agent: AgentView, thread_id: str, review: str
) -> str | None:
    """Let a conversation in or turn it away; the word for why not when the
    node did not."""

    answer = await controls.outcome(
        agent.computer_id,
        {
            "write": "review",
            "agent_id": agent.id,
            "thread_id": thread_id,
            "review": review,
        },
        online=agent.status != "offline",
    )
    return answer if isinstance(answer, str) else None


async def reminders(
    controls: Controls, agent: AgentView, contact: Contact
) -> Reminders:
    """The reminders still to come in a conversation."""

    answer = await controls.outcome(
        agent.computer_id,
        {"read": "reminders", "agent_id": agent.id, "actor_id": contact.actor_id},
        online=agent.status != "offline",
    )
    if isinstance(answer, str):
        word, _, code = answer.partition(":")
        return Reminders((), word, code or None)
    return Reminders(
        tuple(
            Reminder(item["title"], item["next_fire_at_ms"], item["repeat_rule"])
            for item in answer["reminders"]
            if item["owner_thread_id"] == contact.thread_id
        ),
        "listed",
    )


__all__ = [
    "REVIEW_REPLY",
    "Reminder",
    "Reminders",
    "Request",
    "decide",
    "reminders",
    "request",
]
