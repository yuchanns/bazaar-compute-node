from __future__ import annotations

from enum import StrEnum

from .actor import Actor


class State(StrEnum):
    # the Agent as a whole: not up yet, or done for
    INIT = "init"
    TERMINATED = "terminated"
    # the Agent as one actor sees it while it is up
    IDLE = "idle"
    WORKING = "working"
    RECOVERING = "recovering"
    FAILED = "failed"


class Agent:
    """One Agent's condition, advanced by its runtime and its channel, and
    by whoever starts and stops it.

    The runtime is how an Agent is implemented and the channel is how it faces
    the outside, so both push it between states. A runtime that refused leaves
    the Agent FAILED and one that went quiet leaves it RECOVERING: the first
    says the turn did not run, the second that nobody knows. Either way the
    next runtime to come up returns the Agent to rest.

    Before any of that the Agent is INIT, and once stopped it is TERMINATED
    for good; both are its own and cover every actor, so nothing starts a
    turn on an Agent that is not up. The turn to TERMINATED is taken before
    the stopping is awaited, so a look in the meantime already sees it.
    """

    def __init__(self, states: dict[Actor, State]) -> None:
        self._states = states
        self._lifecycle = State.INIT

    @property
    def lifecycle(self) -> State:
        return self._lifecycle

    @property
    def up(self) -> bool:
        return self._lifecycle is State.IDLE

    def get(self, actor: Actor) -> State:
        if not self.up:
            return self._lifecycle
        return self._states.get(actor, State.IDLE)

    def started(self) -> None:
        if self._lifecycle is State.TERMINATED:
            raise RuntimeError("Agent is terminated")
        self._lifecycle = State.IDLE

    def terminated(self) -> None:
        self._lifecycle = State.TERMINATED

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
