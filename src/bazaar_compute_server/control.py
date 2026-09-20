"""Requests handed down to computers, and their answers coming back.

A page asks a computer something; the node fetches it in a long poll, runs it,
and answers through its event stream. Between the two, this holds who is
waiting for what: one process, one set of waiters, and nothing kept once no
one waits any more."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .clock import now_ms
from .protocol import Event
from .storage import Computer

# how long a page waits for a computer to answer before it says the computer
# did not, and how long a node's poll is held before it is answered with nothing
ANSWER_SECONDS = 10
POLL_SECONDS = 25
# how many requests one poll hands over at most
POLL_LIMIT = 20


@dataclass(slots=True)
class _Asked:
    computer_id: str
    update_id: int
    request: Mapping[str, Any]
    answer: asyncio.Future[Mapping[str, Any]]
    # how many askers wait on the answer; none, and the request is forgotten
    waiters: int = 0


class Controls:
    def __init__(self) -> None:
        # what each computer has been asked and not yet answered, by update id
        self._asked: defaultdict[str, dict[int, _Asked]] = defaultdict(dict)
        # the same request to the same computer, while one is out, is asked
        # once: everyone waits on that one
        self._in_flight: dict[tuple[str, str], _Asked] = {}
        # a poll waits here until its computer has something
        self._arrivals: defaultdict[str, asyncio.Condition] = defaultdict(
            asyncio.Condition
        )
        # ids climb with the clock, so a node's offset from before a restart
        # of the server is still behind what comes after it
        self._last_id = 0
        self._closing = False

    async def ask(
        self,
        computer_id: str,
        request: Mapping[str, Any],
        *,
        timeout: float = ANSWER_SECONDS,
    ) -> Mapping[str, Any] | None:
        """The computer's answer to the request, or nothing when it did not
        answer in time; a request nobody waits for any more is forgotten."""

        key = (computer_id, json.dumps(request, sort_keys=True, separators=(",", ":")))
        asked = self._in_flight.get(key)
        if asked is None:
            update_id = self._last_id = max(self._last_id + 1, now_ms())
            asked = _Asked(
                computer_id,
                update_id,
                request,
                asyncio.get_running_loop().create_future(),
            )
            self._in_flight[key] = asked
            self._asked[computer_id][update_id] = asked
            asked.answer.add_done_callback(lambda _: self._forget(key, asked))
            arrivals = self._arrivals[computer_id]
            async with arrivals:
                arrivals.notify_all()
        asked.waiters += 1
        try:
            # shielded: one asker giving up must not take the answer from the
            # others still waiting on it
            return await asyncio.wait_for(asyncio.shield(asked.answer), timeout)
        except TimeoutError:
            return None
        finally:
            asked.waiters -= 1
            if asked.waiters == 0 and not asked.answer.done():
                self._forget(key, asked)

    def _forget(self, key: tuple[str, str], asked: _Asked) -> None:
        if self._in_flight.get(key) is asked:
            del self._in_flight[key]
        self._asked[asked.computer_id].pop(asked.update_id, None)

    async def updates(self, computer_id: str, *, after: int) -> list[Mapping[str, Any]]:
        """The computer's requests past `after`, waiting a while for one to
        arrive when there is none yet."""

        pending = self._pending(computer_id, after)
        if not pending and not self._closing:
            arrivals = self._arrivals[computer_id]
            async with arrivals:
                try:
                    await asyncio.wait_for(arrivals.wait(), POLL_SECONDS)
                except TimeoutError:
                    return []
            if self._closing:
                return []
            pending = self._pending(computer_id, after)
        return pending

    def _pending(self, computer_id: str, after: int) -> list[Mapping[str, Any]]:
        asked = self._asked[computer_id]
        pending: list[Mapping[str, Any]] = [
            {"update_id": update_id, "request": asked[update_id].request}
            for update_id in sorted(asked)
            if update_id > after
        ]
        return pending[:POLL_LIMIT]

    async def close(self) -> None:
        """Let every held poll go: the server is going down."""

        self._closing = True
        for arrivals in list(self._arrivals.values()):
            async with arrivals:
                arrivals.notify_all()

    def result(self, computer: Computer, event: Event) -> None:
        """A `control.result` event came in: the answer it carries, as text,
        goes to whoever waits for it."""

        update_id = event.metadata.get("update_id")
        response = event.metadata.get("response")
        if isinstance(update_id, int) and isinstance(response, str):
            self.answered(computer.id, update_id, json.loads(response))

    def answered(
        self, computer_id: str, update_id: int, response: Mapping[str, Any]
    ) -> None:
        """An answer came in; whoever still waits for it has it."""

        asked = self._asked[computer_id].get(update_id)
        if asked is not None and not asked.answer.done():
            asked.answer.set_result(response)


__all__ = ["ANSWER_SECONDS", "POLL_LIMIT", "POLL_SECONDS", "Controls"]
