from __future__ import annotations

from ..actor import Actor, Agent, Thread
from ..command import UnreadSummary
from ..storage import IStorage


async def threads_in_reach(storage: IStorage, actor: Actor) -> tuple[str, ...]:
    """Return the conversations one actor answers for."""

    match actor:
        case Thread(thread_id):
            return (thread_id,)
        case Agent():
            return await storage.list_thread_ids()


async def unread_in_reach(
    storage: IStorage,
    actor: Actor,
    *,
    limit: int,
) -> UnreadSummary:
    """Say what one actor has unread, counted and carried from the same read."""

    match actor:
        case Thread(thread_id):
            return await storage.read_unread_summary(thread_id, limit=limit)
        case Agent():
            return await storage.read_unread_summary(None, limit=limit)
