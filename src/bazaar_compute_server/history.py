"""One conversation's messages, as the agent's node keeps them."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from markupsafe import Markup

from .control import Controls
from .fleet import PAGE_SIZE, AgentView
from .markdown import render
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
    # what was written, read as the Markdown a chat is written in; a system
    # message is the node's own words and stays as they are
    html: Markup | None = None


@dataclass(frozen=True, slots=True)
class Turn:
    """One speaker's run of consecutive messages."""

    speaker: str
    sender_id: str
    kind: str
    at_ms: int
    lines: tuple[Line, ...]
    # a run that goes on across the edge of a page: this turn leads into the
    # one after it that is on the page already, or follows the one before it
    leads: bool = False
    follows: bool = False


@dataclass(frozen=True, slots=True)
class Contact:
    """The conversation as the column names it, from its row in the list."""

    thread_id: str
    # who the node answers the conversation as; a read of it is asked as them
    actor_id: str
    # the conversation as the node's store names it, which a read is asked with
    target: str
    channel: str
    name: str

    @property
    def kind(self) -> str:
        return "group" if self.target.startswith("group:") else "dm"


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
    around: str | None,
) -> History:
    """The page ending at the conversation's latest message - as the column
    named it when it was listed, which may be a while ago; a conversation
    with no message to name is read from its start, which is where it ends."""

    since = await _since(storage, agent, contact)
    answer = await _ask(controls, agent, contact, around=around, limit=PAGE_SIZE)
    if isinstance(answer, str):
        return _failed(agent, contact, since, answer)
    messages, more = answer
    # the page is read around the message the column named: what came after
    # it fills the half past the middle, and a full half may have been cut
    # short, so the tail is left one event behind to ask at once; with none
    # named the whole page is what came after
    anchor = next(
        (index for index, item in enumerate(messages) if item["message_id"] == around),
        -1,
    )
    caught_up = len(messages) - anchor - 1 < PAGE_SIZE // 2 - 1
    return History(
        agent,
        contact,
        _turns(messages),
        messages[0]["message_id"] if messages and more else None,
        messages[-1]["message_id"] if messages else None,
        since if caught_up else since - 1,
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
        return _failed(agent, contact, since, answer, before=before)
    messages, _ = answer
    anchor = next(item for item in messages if item["message_id"] == before)
    messages = [item for item in messages if item["seq"] < anchor["seq"]]
    return History(
        agent,
        contact,
        _turns(messages, leads_into=anchor),
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
    shown: str | None,
) -> History | None:
    """What came after a message, when something is new since the tail last
    asked; nothing otherwise."""

    newer = await news(storage, agent, contact, since=since, shown=shown)
    if newer is None:
        return None
    answer = await _ask(controls, agent, contact, around=after, limit=_WINDOW)
    if isinstance(answer, str):
        return _failed(agent, contact, since, answer, last=after)
    messages, _ = answer
    anchor = next(item for item in messages if item["message_id"] == after)
    messages = [item for item in messages if item["seq"] > anchor["seq"]]
    # a window holds this many past its middle; a fuller one may have been
    # cut short, so the next tick asks again from where this ends
    caught_up = len(messages) < _WINDOW // 2 - 1
    return History(
        agent,
        contact,
        _turns(messages, follows_from=anchor),
        None,
        messages[-1]["message_id"] if messages else after,
        newer if caught_up else since,
        "listed",
    )


async def news(
    storage: IStorage,
    agent: AgentView,
    contact: Contact,
    *,
    since: int,
    shown: str | None,
) -> int | None:
    """The newest message event, when there is one past `since` or the
    computer went offline or came back since the page last said (`shown`)
    how it stood; nothing when nothing is new."""

    newer = await _since(storage, agent, contact)
    if newer <= since and (agent.status == "offline") == (shown == "offline"):
        return None
    return newer


async def _since(storage: IStorage, agent: AgentView, contact: Contact) -> int:
    return await storage.latest_message_event(
        agent.computer_id, agent.id, thread_id=contact.thread_id
    )


async def _ask(
    controls: Controls,
    agent: AgentView,
    contact: Contact,
    *,
    around: str | None,
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
    return messages, result["has_before"]


def _failed(
    agent: AgentView,
    contact: Contact,
    since: int,
    answer: str,
    *,
    before: str | None = None,
    last: str | None = None,
) -> History:
    """A read that got nothing, keeping where it was asked from: the edge or
    tail it answers stays, so it is asked again."""

    answer, _, code = answer.partition(":")
    return History(agent, contact, (), before, last, since, answer, code or None)


def _turns(
    messages: list[dict[str, Any]],
    *,
    leads_into: dict[str, Any] | None = None,
    follows_from: dict[str, Any] | None = None,
) -> tuple[Turn, ...]:
    """The messages as runs of one speaker; the run at either edge is marked
    when the message on the page beyond it (`leads_into` after, `follows_from`
    before) is the same speaker's, so the two read as one."""

    turns: list[Turn] = []
    for item in messages:
        line = Line(
            message_id=item["message_id"],
            seq=item["seq"],
            body=item["body"],
            attachments=tuple(attachment["name"] for attachment in item["attachments"]),
            html=render(item["body"]) if kind != "system" else None,
        )
        if turns and _same(turns[-1], item):
            turns[-1] = replace(turns[-1], lines=(*turns[-1].lines, line))
            continue
        speaker, sender_id, kind = _who(item)
        at_ms = (
            item["provider_time_ms"] or item["received_at_ms"] or item["created_at_ms"]
        )
        turns.append(Turn(speaker, sender_id, kind, at_ms, (line,)))
    if turns and leads_into is not None and _same(turns[-1], leads_into):
        turns[-1] = replace(turns[-1], leads=True)
    if turns and follows_from is not None and _same(turns[0], follows_from):
        turns[0] = replace(turns[0], follows=True)
    return tuple(turns)


def _who(item: dict[str, Any]) -> tuple[str, str, str]:
    """Who said a message: how they are named, and who they are - two people
    may be named alike."""

    sender = item["sender"] or {}
    speaker = sender.get("display_name") or sender.get("name") or sender.get("id") or ""
    return speaker, sender.get("id") or "", item["sender_kind"]


def _same(turn: Turn, item: dict[str, Any]) -> bool:
    return (turn.speaker, turn.sender_id, turn.kind) == _who(item)


__all__ = ["Contact", "History", "Line", "Turn", "earlier", "later", "latest", "news"]
