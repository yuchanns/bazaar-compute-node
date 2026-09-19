"""One conversation's messages, as the agent's node keeps them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contacts import contact_name
from .control import Controls
from .fleet import PAGE_SIZE, AgentView
from .storage import IStorage

# a page of history is read around one message: what the node returns is
# centred on it, so twice the page is asked for and the far half let go
_WINDOW = 2 * PAGE_SIZE


@dataclass(frozen=True, slots=True)
class Line:
    message_id: str
    seq: int
    body: str
    # what came with it, by name only; the files stay on the node
    attachments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Turn:
    """One speaker's run of consecutive messages."""

    speaker: str
    kind: str
    at_ms: int
    lines: tuple[Line, ...]


@dataclass(frozen=True, slots=True)
class Contact:
    """The conversation as the column names it, from its row in the list."""

    thread_id: str
    # who the node answers the conversation as; a read of it is asked as them
    actor_id: str
    target: str
    channel: str

    @property
    def name(self) -> str:
        return contact_name(self.target)

    @property
    def kind(self) -> str:
        return "group" if self.target.startswith(("#", "group:")) else "dm"


@dataclass(frozen=True, slots=True)
class History:
    """What the chat column shows: a stretch of turns, or why it got none.
    `answer` reads as for the contacts column."""

    agent: AgentView
    contact: Contact
    turns: tuple[Turn, ...]
    # the message the stretch starts at, when there may be older ones to
    # read before it
    before: str | None
    # the message it ends at, which a later stretch is read after
    last: str | None
    # the newest message event known when this was read; the tail is only
    # asked for again once a newer one has arrived
    since: int
    answer: str
    code: str | None = None


async def latest(
    storage: IStorage,
    controls: Controls,
    agent: AgentView,
    contact: Contact,
    *,
    around: str,
) -> History:
    """The page ending at the conversation's latest message."""

    since = await _since(storage, agent, contact)
    answer = await _ask(controls, agent, contact, around=around, limit=PAGE_SIZE)
    if isinstance(answer, str):
        return _failed(agent, contact, since, answer)
    messages, more = answer
    return History(
        agent,
        contact,
        _turns(messages),
        messages[0]["message_id"] if messages and more else None,
        messages[-1]["message_id"] if messages else None,
        since,
        "listed",
    )


async def earlier(
    storage: IStorage,
    controls: Controls,
    agent: AgentView,
    contact: Contact,
    *,
    before: str,
) -> History:
    """The page before a message: the half of a window around it that came
    first."""

    since = await _since(storage, agent, contact)
    answer = await _ask(controls, agent, contact, around=before, limit=_WINDOW)
    if isinstance(answer, str):
        return _failed(agent, contact, since, answer)
    messages, _ = answer
    anchor = next(item["seq"] for item in messages if item["message_id"] == before)
    messages = [item for item in messages if item["seq"] < anchor]
    return History(
        agent,
        contact,
        _turns(messages),
        messages[0]["message_id"] if len(messages) >= PAGE_SIZE else None,
        None,
        since,
        "listed",
    )


async def later(
    storage: IStorage,
    controls: Controls,
    agent: AgentView,
    contact: Contact,
    *,
    after: str,
    since: int,
) -> History | None:
    """What came after a message, when a message event newer than `since`
    says something did; nothing otherwise."""

    newer = await _since(storage, agent, contact)
    if newer <= since:
        return None
    answer = await _ask(controls, agent, contact, around=after, limit=_WINDOW)
    if isinstance(answer, str):
        return _failed(agent, contact, since, answer)
    messages, _ = answer
    anchor = next(item["seq"] for item in messages if item["message_id"] == after)
    messages = [item for item in messages if item["seq"] > anchor]
    # a window holds this many past its middle; a fuller one may have been
    # cut short, so the next tick asks again from where this ends
    caught_up = len(messages) < _WINDOW // 2 - 1
    return History(
        agent,
        contact,
        _turns(messages),
        None,
        messages[-1]["message_id"] if messages else after,
        newer if caught_up else since,
        "listed",
    )


async def _since(storage: IStorage, agent: AgentView, contact: Contact) -> int:
    return await storage.latest_message_event(
        agent.computer_id, agent.id, thread_id=contact.thread_id
    )


async def _ask(
    controls: Controls,
    agent: AgentView,
    contact: Contact,
    *,
    around: str,
    limit: int,
) -> tuple[list[dict[str, Any]], bool] | str:
    """The messages around one, oldest first, and whether the conversation
    goes on before them; or the word for why there are none."""

    if agent.status == "offline":
        return "offline"
    answer = await controls.ask(
        agent.computer_id,
        {
            "read": "history",
            "agent_id": agent.id,
            "actor_id": contact.actor_id,
            "target": contact.target,
            "around_message_id": around,
            "limit": limit,
        },
    )
    if answer is None:
        return "silent"
    if not answer.get("ok"):
        return f"refused:{answer.get('code')}"
    result = answer["result"]
    messages: list[dict[str, Any]] = result["messages"]
    return messages, bool(messages) and messages[0]["seq"] > 1


def _failed(agent: AgentView, contact: Contact, since: int, answer: str) -> History:
    answer, _, code = answer.partition(":")
    return History(agent, contact, (), None, None, since, answer, code or None)


def _turns(messages: list[dict[str, Any]]) -> tuple[Turn, ...]:
    turns: list[Turn] = []
    for item in messages:
        sender = item["sender"] or {}
        speaker = (
            sender.get("display_name") or sender.get("name") or sender.get("id") or ""
        )
        kind = item["sender_kind"]
        line = Line(
            message_id=item["message_id"],
            seq=item["seq"],
            body=item["body"],
            attachments=tuple(attachment["name"] for attachment in item["attachments"]),
        )
        if turns and turns[-1].speaker == speaker and turns[-1].kind == kind:
            turns[-1] = Turn(speaker, kind, turns[-1].at_ms, (*turns[-1].lines, line))
            continue
        at_ms = (
            item["provider_time_ms"] or item["received_at_ms"] or item["created_at_ms"]
        )
        turns.append(Turn(speaker, kind, at_ms, (line,)))
    return tuple(turns)


__all__ = ["Contact", "History", "Line", "Turn", "earlier", "later", "latest"]
