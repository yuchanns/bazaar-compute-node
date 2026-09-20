"""What every family of commands is built on: the store, the audit, the
clock, and who the agent's conversations are answered by."""

from __future__ import annotations

from collections.abc import Callable

from ...actor import Actor, Actors, Agent, Thread
from ...audit import AuditRecorder
from ...concurrency import IThreadConcurrency
from ...correlation import CorrelationContext
from ...storage import InboxTargetResolutionError, IStorage


class Commands:
    """The families are mixins over this: each adds its own commands and,
    through a `_with_*` method, what only it needs; the assembled service
    calls those once, after this."""

    def __init__(
        self,
        *,
        actors: Actors,
        storage: IStorage,
        audit: AuditRecorder,
        concurrency: IThreadConcurrency,
        clock: Callable[[], int],
    ) -> None:
        self._actors = actors
        self._storage = storage
        self._audit = audit
        self._concurrency = concurrency
        self._clock = clock

    def _require_in_reach(
        self,
        actor: Actor,
        target_thread_id: str,
        raw_target: str,
    ) -> None:
        """Refuse a target this actor does not answer for."""

        match actor:
            case Agent():
                return
            case Thread(id) if id == target_thread_id:
                return
            case Thread():
                raise InboxTargetResolutionError(
                    f"inbox target is not this conversation: {raw_target}"
                )

    def _correlation(
        self,
        *,
        thread_id: str | None = None,
        actor: Actor | None = None,
        channel: str | None = None,
        channel_session_id: str | None = None,
        inbound_seq: int | None = None,
        outbound_message_id: str | None = None,
    ) -> CorrelationContext:
        return CorrelationContext(
            node_id=self._actors.agent_id,
            channel=channel,
            channel_session_id=channel_session_id,
            thread_id=thread_id,
            actor=actor,
            inbound_seq=inbound_seq,
            outbound_message_id=outbound_message_id,
        )


__all__ = ["Commands"]
