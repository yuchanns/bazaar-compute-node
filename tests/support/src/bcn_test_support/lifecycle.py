from __future__ import annotations

import asyncio

from bazaar_compute_node.core.actor import Thread
from bazaar_compute_node.core.agent import State
from bazaar_compute_node.core.channel import ChannelSendRequest
from bazaar_compute_node.core.orchestration import SessionOrchestrator

from .channel import TestChannel


async def wait_for_turn_terminal(
    *,
    orchestrator: SessionOrchestrator,
    channel: TestChannel,
    session_id: str,
    client_user_message_id: str,
    sent_after: int,
    timeout: float = 600,
    expect_runtime_discarded: bool = False,
) -> tuple[ChannelSendRequest, ...]:
    """Wait for provider-neutral outbound, turn, and session-runtime lifecycle completion."""

    if not session_id:
        raise ValueError("session_id must be a non-empty string")
    if not client_user_message_id:
        raise ValueError("client_user_message_id must be a non-empty string")
    if (
        isinstance(sent_after, bool)
        or not isinstance(sent_after, int)
        or sent_after < 0
    ):
        raise ValueError("sent_after must be a non-negative integer")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive number")

    outbound: tuple[ChannelSendRequest, ...] = ()
    active_turn = False
    state = orchestrator.session_runtime_state(Thread(session_id))
    try:
        async with asyncio.timeout(timeout):
            while True:
                outbound = tuple(
                    message
                    for message in channel.sent_messages[sent_after:]
                    if message.session_id == session_id
                )
                active_turn = any(
                    turn.client_user_message_id == client_user_message_id
                    for turn in orchestrator._runtime_turns.values()  # pyright: ignore[reportPrivateUsage]
                )
                state = orchestrator.session_runtime_state(Thread(session_id))
                lifecycle_terminal = (
                    state is None
                    and orchestrator.runtime_session(Thread(session_id)) is None
                    if expect_runtime_discarded
                    else state is State.IDLE
                )
                if outbound and lifecycle_terminal and not active_turn:
                    return outbound
                await asyncio.sleep(0.05)
    except TimeoutError as error:
        raise AssertionError(
            "turn did not reach the provider-neutral terminal contract: "
            f"session_id={session_id!r}, outbound_count={len(outbound)}, "
            f"session_runtime_state={state!r}, active_turn={active_turn}, "
            f"runtime_live={orchestrator.runtime_session(Thread(session_id)) is not None}"
        ) from error
