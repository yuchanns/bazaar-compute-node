"""The commands an operator gives from outside: who may talk to the agent,
who has, and what the agent is set to say."""

from __future__ import annotations

from ...models import ChannelSession, Review, RuntimeEventState
from .base import Commands


class ReviewCommands(Commands):
    async def review_contact(self, thread_id: str, decision: Review) -> ChannelSession:
        async with self._concurrency.for_thread(thread_id):
            session = await self._storage.set_review(
                thread_id, decision, now_ms=self._clock()
            )
        target = await self._storage.resolve_inbox_target(session.canonical_target)
        await self._audit.append(
            event_name="channel.session.reviewed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(
                thread_id=thread_id,
                channel=session.channel,
                channel_session_id=session.id,
            ),
            metadata={
                "thread_id": thread_id,
                "review": decision.value,
                "target": session.canonical_target,
                "target_name": target.display_target,
            },
        )
        return session

    async def setting(self, key: str) -> str | None:
        return await self._storage.get_setting(key)

    async def set_setting(self, key: str, value: str) -> None:
        await self._storage.set_setting(key, value, now_ms=self._clock())
        # the value is not recorded: it is the operator's words, not a fact
        await self._audit.append(
            event_name="setting.changed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(),
            metadata={"key": key},
        )


__all__ = ["ReviewCommands"]
