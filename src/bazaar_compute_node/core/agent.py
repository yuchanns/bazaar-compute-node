from __future__ import annotations

from enum import StrEnum

from .actor import Actor


class State(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    RECOVERING = "recovering"
    FAILED = "failed"


class Agent:
    """One Agent's condition, advanced by its runtime and its channel.

    The runtime is how an Agent is implemented and the channel is how it faces
    the outside, so both push it between states. A runtime that refused leaves
    the Agent FAILED and one that went quiet leaves it RECOVERING: the first
    says the turn did not run, the second that nobody knows. Either way the
    next runtime to come up returns the Agent to rest.
    """

    def __init__(self, states: dict[Actor, State]) -> None:
        self._states = states

    def get(self, actor: Actor) -> State:
        return self._states.get(actor, State.IDLE)

    def forget(self, actor: Actor) -> None:
        self._states.pop(actor, None)

    def started_turn(self, actor: Actor) -> State:
        return self._enter(actor, State.WORKING)

    def finished_turn(self, actor: Actor) -> State:
        return self._enter(actor, State.IDLE)

    def lost_runtime(self, actor: Actor) -> State:
        return self._enter(actor, State.RECOVERING)

    def refused_runtime(self, actor: Actor) -> State:
        return self._enter(actor, State.FAILED)

    def _enter(self, actor: Actor, state: State) -> State:
        self._states[actor] = state
        return state


__all__ = ["Agent", "State"]
