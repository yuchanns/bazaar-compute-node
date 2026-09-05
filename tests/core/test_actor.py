from __future__ import annotations

import pytest

from bazaar_compute_node.core.actor import Actors, Agent, Mode, Thread


def test_a_session_actor_is_the_conversation_itself() -> None:
    actors = Actors(agent_id="agent-1", mode=Mode.SESSION)

    assert actors.for_thread("thread-a") == Thread("thread-a")
    assert actors.for_thread("thread-b") == Thread("thread-b")
    assert actors.resolve("thread-a") == Thread("thread-a")


def test_an_individual_actor_is_the_agent_for_every_conversation() -> None:
    actors = Actors(agent_id="agent-1", mode=Mode.DANGEROUS_INDIVIDUAL)

    assert actors.for_thread("thread-a") == Agent("agent-1")
    assert actors.for_thread("thread-b") == Agent("agent-1")
    assert actors.resolve("agent-1") == Agent("agent-1")


def test_an_individual_agent_owns_no_actor_but_itself() -> None:
    actors = Actors(agent_id="agent-1", mode=Mode.DANGEROUS_INDIVIDUAL)

    with pytest.raises(ValueError, match="unknown actor"):
        actors.resolve("thread-a")


def test_an_actor_carries_the_id_it_stands_for() -> None:
    for actors in (
        Actors(agent_id="agent-1", mode=Mode.SESSION),
        Actors(agent_id="agent-1", mode=Mode.DANGEROUS_INDIVIDUAL),
    ):
        match actors.for_thread("thread-a"):
            case Agent(id) | Thread(id):
                assert id
