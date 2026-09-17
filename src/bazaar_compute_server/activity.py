"""An agent's recent activity in words, and what it has used today."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import tzinfo

from .clock import clock_text, start_of_today_ms
from .fields import integer, text
from .i18n import Translator
from .storage import IStorage, StoredEvent

CARD_EVENTS = 5


@dataclass(frozen=True, slots=True)
class ActivityLine:
    time: str
    text: str


@dataclass(frozen=True, slots=True)
class UsageView:
    tokens: str
    cost: str


async def recent_lines(
    storage: IStorage,
    translator: Translator,
    tz: tzinfo,
    computer_id: str,
    agent_id: str,
) -> list[ActivityLine]:
    recent = await storage.recent_activity(computer_id, agent_id, limit=CARD_EVENTS)
    return [
        ActivityLine(
            time=clock_text(item.created_at_ms, tz), text=event_text(translator, item)
        )
        for item in recent
    ]


async def usage_today(
    storage: IStorage, tz: tzinfo, computer_id: str, agent_id: str
) -> UsageView:
    rows = await storage.usage_since(computer_id, agent_id, start_of_today_ms(tz))
    return _usage_view(rows)


def event_text(translator: Translator, item: StoredEvent) -> str:
    key = f"event.{item.event_name}"
    words = translator.text(key) if translator.has(key) else item.event_name
    metadata = item.payload.get("metadata", {})
    if item.event_name.startswith("tool_call.") and text(metadata.get("name")):
        return f"{words} · {metadata['name']}"
    if item.event_name == "channel.inbound.persisted":
        name = text(metadata.get("target_name")) or text(metadata.get("target"))
        return f"{words} · {name}" if name else words
    if item.event_name == "usage.updated":
        total = metadata.get("total")
        tokens = (
            integer(total.get("total_tokens")) if isinstance(total, Mapping) else None
        )
        return f"{words} · {tokens:,}" if tokens is not None else words
    return words


def _usage_view(rows: Sequence[StoredEvent]) -> UsageView:
    tokens = 0
    cost = 0.0
    for item in rows:
        metadata = item.payload.get("metadata", {})
        total = metadata.get("total")
        if isinstance(total, Mapping):
            tokens += integer(total.get("total_tokens")) or 0
        cost += float(metadata.get("cost_usd") or 0)
    return UsageView(tokens=_compact(tokens), cost=f"{cost:.2f}")


def _compact(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.0f}K"
    return str(tokens)


__all__ = ["ActivityLine", "UsageView", "event_text", "recent_lines", "usage_today"]
