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
    the outside, so both push it between states. A turn that ran out of
    runtimes leaves the Agent in FAILED until the channel has carried that
    outcome out, which is the only edge back to IDLE from there.
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
        if self.get(session_id) is State.FAILED:
            return State.FAILED
        return self._enter(session_id, State.IDLE)

    def lost_runtime(self, session_id: str) -> State:
        return self._enter(session_id, State.RECOVERING)

    def exhausted_runtimes(self, session_id: str) -> State:
        return self._enter(session_id, State.FAILED)

    def reported_failure(self, session_id: str) -> State:
        return self._enter(session_id, State.IDLE)

    def _enter(self, session_id: str, state: State) -> State:
        self._states[session_id] = state
        return state


__all__ = ["Agent", "State"]
