from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Mode(StrEnum):
    """How much of an Agent one runtime answers for."""

    SESSION = "session"
    DANGEROUS_INDIVIDUAL = "dangerous_individual"


@dataclass(frozen=True, slots=True)
class Agent:
    """The Agent itself: every conversation it owns is in reach."""

    id: str


@dataclass(frozen=True, slots=True)
class Thread:
    """One conversation, and nothing else is in reach."""

    id: str


Actor = Agent | Thread


@dataclass(frozen=True, slots=True)
class Actors:
    """Say who answers for a conversation, and read that answer back.

    An actor is whoever a runtime answers for: under `session` one
    conversation, under `dangerous_individual` the Agent itself. Which of the
    two a given id stands for is settled here and then carried in the type, so
    nothing downstream has to ask again.
    """

    agent_id: str
    mode: Mode

    def for_thread(self, thread_id: str) -> Actor:
        """Return the actor a conversation's messages are answered by."""

        match self.mode:
            case Mode.SESSION:
                return Thread(thread_id)
            case Mode.DANGEROUS_INDIVIDUAL:
                return Agent(self.agent_id)

    def resolve(self, actor_id: str) -> Actor:
        """Return the actor an id crossing a process boundary stands for."""

        match self.mode:
            case Mode.SESSION:
                return Thread(actor_id)
            case Mode.DANGEROUS_INDIVIDUAL:
                if actor_id != self.agent_id:
                    raise ValueError(f"unknown actor: {actor_id}")
                return Agent(actor_id)


__all__ = ["Actor", "Actors", "Agent", "Mode", "Thread"]
