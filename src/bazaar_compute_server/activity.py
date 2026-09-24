"""An agent's recent activity in words, and what it has used today."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import tzinfo

from .clock import clock_text, start_of_today_ms
from .i18n import Translator
from .storage import IStorage, StoredEvent

CARD_EVENTS = 5


@dataclass(frozen=True, slots=True)
class ActivityLine:
    time: str
    text: str


@dataclass(frozen=True, slots=True)
class UsageView:
    """What an agent used today: the tokens it read fresh, read from cache
    and wrote, and what that cost where its runtime prices it."""

    input: str
    output: str
    cached: str
    cost: str | None


@dataclass(frozen=True, slots=True)
class _Counts:
    input: int = 0
    output: int = 0
    cached: int = 0
    cost: float | None = None

    def since(self, earlier: _Counts) -> _Counts:
        return _Counts(
            self.input - earlier.input,
            self.output - earlier.output,
            self.cached - earlier.cached,
            None if self.cost is None else self.cost - (earlier.cost or 0.0),
        )

    def plus(self, other: _Counts) -> _Counts:
        return _Counts(
            self.input + other.input,
            self.output + other.output,
            self.cached + other.cached,
            (
                None
                if self.cost is None and other.cost is None
                else (self.cost or 0.0) + (other.cost or 0.0)
            ),
        )


# what the card does not read out: the beat, the running total the card sums
# up below anyway, the reads a viewer of this very page causes on the node,
# which would otherwise fill it with themselves, and the tool's word on a
# send, which the delivery record says already - only the hold that kept a
# send back has no record of its own
QUIET = (
    "node.health",
    "usage.updated",
    "control.result",
    "tool.bcc.inbox.check.completed",
    "tool.bcc.message.read.completed",
    *(
        f"tool.bcc.message.send.{state}"
        for state in ("pending", "queued", "sent", "partial", "failed", "unknown")
    ),
)


async def recent_lines(
    storage: IStorage,
    translator: Translator,
    tz: tzinfo,
    computer_id: str,
    agent_id: str,
) -> list[ActivityLine]:
    recent = await storage.recent_activity(
        computer_id, agent_id, limit=CARD_EVENTS, skipping=QUIET
    )
    return [
        ActivityLine(
            time=clock_text(item.created_at_ms, tz), text=event_text(translator, item)
        )
        for item in recent
    ]


# how many events a page of the activity tab holds
PAGE_EVENTS = 50


@dataclass(frozen=True, slots=True)
class EventLine:
    """One event of an agent's stream as the activity tab reads it."""

    id: int
    at_ms: int
    # where it came from: the first part of the event's name, as named
    source: str
    text: str
    detail: str
    failed: bool


async def event_lines(
    storage: IStorage,
    translator: Translator,
    computer_id: str,
    agent_id: str,
    *,
    before: int | None = None,
    after: int | None = None,
) -> list[EventLine]:
    """An agent's events, newest first, a page at a time: those older than
    `before`, or those newer than `after`; the same ones left out as on the
    card."""

    return [
        EventLine(
            item.id,
            item.created_at_ms,
            item.event_name.partition(".")[0],
            *_parts(translator, item),
            item.event_name.endswith(".failed"),
        )
        for item in await storage.recent_activity(
            computer_id,
            agent_id,
            limit=PAGE_EVENTS,
            skipping=QUIET,
            before=before,
            after=after,
        )
    ]


async def usage_today(
    storage: IStorage, tz: tzinfo, computer_id: str, agent_id: str
) -> UsageView | None:
    """Today's usage, or nothing when there was none to speak of."""

    midnight = start_of_today_ms(tz)
    rows = await storage.usage_around(computer_id, agent_id, midnight)
    # a session that ran across midnight already counted part of its total
    # yesterday; today's share is what it has grown since
    before = {
        _session(item): _usage(item) for item in rows if item.created_at_ms < midnight
    }
    today = _Counts()
    for item in rows:
        if item.created_at_ms < midnight:
            continue
        today = today.plus(_usage(item).since(before.get(_session(item), _Counts())))
    if not (today.input or today.output or today.cached) and today.cost is None:
        return None
    return UsageView(
        input=_compact(today.input),
        output=_compact(today.output),
        cached=_compact(today.cached),
        cost=None if today.cost is None else f"{today.cost:.2f}",
    )


def event_text(translator: Translator, item: StoredEvent) -> str:
    return " · ".join(part for part in _parts(translator, item) if part)


def _parts(translator: Translator, item: StoredEvent) -> tuple[str, str]:
    """What happened, in words, and what it happened to."""

    key = f"event.{item.event_name}"
    words = translator.text(key) if translator.has(key) else item.event_name
    metadata = item.payload["metadata"]
    if item.event_name.startswith("tool_call."):
        return words, metadata.get("name") or ""
    if item.event_name == "channel.inbound.persisted":
        sender = metadata.get("sender") or {}
        return words, " · ".join(
            part
            for part in (
                metadata.get("target_name") or metadata.get("target"),
                sender.get("display_name") or sender.get("name"),
            )
            if part
        )
    if item.event_name == "usage.updated":
        tokens = metadata.get("total", {}).get("total_tokens")
        return words, f"{tokens:,}" if tokens is not None else ""
    return words, ""


def _session(item: StoredEvent) -> str | None:
    return item.payload["correlation"].get("runtime_session_id")


def _usage(item: StoredEvent) -> _Counts:
    """A session's running totals, by kind: what was written into the cache
    was read fresh first, so it counts as input; reasoning is part of the
    output already."""

    metadata = item.payload["metadata"]
    total = metadata.get("total", {})
    return _Counts(
        input=(total.get("input_tokens") or 0)
        + (total.get("cache_write_input_tokens") or 0),
        output=total.get("output_tokens") or 0,
        cached=total.get("cached_input_tokens") or 0,
        cost=metadata.get("cost_usd"),
    )


# thousands, millions, billions, trillions: the last reads up to the largest
# count the door lets in
_STEPS = (
    (1_000_000_000_000, "T"),
    (1_000_000_000, "B"),
    (1_000_000, "M"),
)


def _compact(tokens: int) -> str:
    for size, letter in _STEPS:
        if tokens >= size:
            return f"{tokens / size:.1f}{letter}"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.0f}K"
    return str(tokens)


__all__ = [
    "PAGE_EVENTS",
    "ActivityLine",
    "EventLine",
    "UsageView",
    "event_lines",
    "event_text",
    "recent_lines",
    "usage_today",
]
