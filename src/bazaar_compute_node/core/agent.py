from __future__ import annotations

from enum import StrEnum


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

    def __init__(self, states: dict[str, State]) -> None:
        self._states = states

    def get(self, session_id: str) -> State:
        return self._states.get(session_id, State.IDLE)

    def forget(self, session_id: str) -> None:
        self._states.pop(session_id, None)

    def started_turn(self, session_id: str) -> State:
        return self._enter(session_id, State.WORKING)

    def finished_turn(self, session_id: str) -> State:
        return self._enter(session_id, State.IDLE)

    def lost_runtime(self, session_id: str) -> State:
        return self._enter(session_id, State.RECOVERING)

    def refused_runtime(self, session_id: str) -> State:
        return self._enter(session_id, State.FAILED)

    def _enter(self, session_id: str, state: State) -> State:
        self._states[session_id] = state
        return state


__all__ = ["Agent", "State"]
