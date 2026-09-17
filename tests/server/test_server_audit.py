from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from uuid import uuid7

import aiosqlite
import pytest

from bazaar_compute_node.contrib.server.audit import BATCH_BYTES, ServerAudit
from bazaar_compute_node.core.audit import AuditEvent
from bazaar_compute_node.core.correlation import CorrelationContext
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import RuntimeEventState

from ._serving import enrol, free_port, serving

BUDGET = TimeoutBudget(
    startup_seconds=5, provider_call_seconds=5, command_seconds=5, shutdown_seconds=1
)


def _event(
    name: str, at_ms: int, text: str | None = None, agent_id: str = "agent-1"
) -> AuditEvent:
    return AuditEvent(
        event_name=name,
        state=RuntimeEventState.COMPLETED,
        created_at_ms=at_ms,
        correlation=CorrelationContext(node_id=agent_id, thread_id="t-1"),
        metadata={"text": name if text is None else text},
    )


async def _wait_until(predicate: Callable[[], bool], seconds: float = 10) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


async def _stored(path: Path) -> list[tuple[int, str]]:
    async with (
        aiosqlite.connect(path) as connection,
        connection.execute("SELECT seq, event_name FROM events ORDER BY seq") as cursor,
    ):
        return [(row[0], row[1]) async for row in cursor]


def _sink(url: object) -> ServerAudit:
    return ServerAudit(
        {"url": url, "token_env": "BCN_SERVER_TOKEN"}, timeout_budget=BUDGET
    )


@pytest.mark.asyncio
async def test_events_reach_the_server_in_order_under_the_node_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with serving(tmp_path) as (base, storage):
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        audit = _sink(base + "/")
        try:
            await audit.start(timeout=1)
            for index in range(3):
                await audit.append(_event(f"event.{index}", at_ms=index), timeout=1)
            # case: a value that is not JSON by itself, as a receipt may carry, goes as text
            odd = _event("event.odd", at_ms=3)
            await audit.append(
                replace(odd, metadata={"path": Path("/tmp/x"), "id": uuid7()}),
                timeout=1,
            )
            await _wait_until(lambda: audit.health["sent"] == 4)

            # case: the server holds them in order, attributed to the agent
            assert await _stored(tmp_path / "bcs.sqlite3") == [
                (1, "event.0"),
                (2, "event.1"),
                (3, "event.2"),
                (4, "event.odd"),
            ]
            health = audit.health
            assert health["queued"] == 0 and health["dropped"] == 0
            assert health["last_error"] is None
            assert isinstance(health["last_sent_at_ms"], int)
        finally:
            await audit.stop(timeout=1)


@pytest.mark.asyncio
async def test_what_the_server_cannot_take_is_dropped_and_the_node_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = free_port()
    # the sink starts before the server exists: the first report goes nowhere
    async with serving(tmp_path) as (_, storage):
        enrolment = await enrol(storage, "kana")
    monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
    audit = _sink(f"http://127.0.0.1:{port}")
    try:
        await audit.start(timeout=1)
        await audit.append(_event("event.first", at_ms=1), timeout=1)
        await _wait_until(lambda: audit.health["dropped"] == 1)
        # case: the event is let go, the sink says why, nothing waits on the server
        assert audit.health["queued"] == 0 and audit.health["sent"] == 0
        assert audit.health["last_error"]

        async with serving(tmp_path, port=port):
            await audit.append(_event("event.second", at_ms=2), timeout=1)
            await _wait_until(lambda: audit.health["sent"] == 1)
            # case: once the server is there, what comes next arrives
            assert await _stored(tmp_path / "bcs.sqlite3") == [(2, "event.second")]
            assert audit.health["last_error"] is None
    finally:
        await audit.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_batch_the_server_refuses_is_dropped_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with serving(tmp_path) as (base, storage):
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        audit = _sink(base)
        try:
            await audit.start(timeout=1)
            # case: an event too large for any request is let go at the door,
            # then one the server will not read
            await audit.append(
                _event("event.huge", at_ms=1, text="x" * BATCH_BYTES), timeout=1
            )
            assert audit.health["dropped"] == 1
            assert "larger than a request" in str(audit.health["last_error"])
            await audit.append(
                _event("event.bad", at_ms=2, agent_id="not an id"), timeout=1
            )
            await _wait_until(lambda: audit.health["dropped"] == 2)
            # the server's own reason comes along with the status
            assert "400 invalid_request" in str(audit.health["last_error"])

            # case: the next event is not held up by either
            await audit.append(_event("event.after", at_ms=3), timeout=1)
            await _wait_until(lambda: audit.health["sent"] == 1)
            assert await _stored(tmp_path / "bcs.sqlite3") == [(3, "event.after")]
            assert audit.health["queued"] == 0

            # case: the computer is removed while the node runs: its reports are refused, not kept
            await storage.remove_computer(enrolment.computer.id)
            await audit.append(_event("event.orphan", at_ms=4), timeout=1)
            await _wait_until(lambda: audit.health["dropped"] == 3)
            assert "401" in str(audit.health["last_error"])
            assert audit.health["queued"] == 0
        finally:
            await audit.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_backlog_goes_over_in_requests_the_server_will_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with serving(tmp_path) as (base, storage):
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        audit = _sink(base)
        # each event is a fifth of a request; while nothing is being sent the
        # queue holds one request's worth and lets the rest go
        text = "x" * (BATCH_BYTES // 5)
        try:
            for index in range(10):
                await audit.append(
                    _event(f"event.{index}", at_ms=index, text=text), timeout=1
                )
            queued, dropped = audit.health["queued"], audit.health["dropped"]
            assert isinstance(queued, int) and isinstance(dropped, int)
            assert queued + dropped == 10
            assert 1 < queued < 10
            await audit.start(timeout=1)
            await _wait_until(lambda: audit.health["sent"] == queued)

            # case: what was kept arrives whole and in order, no request refused as too large
            stored = [seq for seq, _ in await _stored(tmp_path / "bcs.sqlite3")]
            assert stored == list(range(1, queued + 1))
            assert "http" not in str(audit.health["last_error"])
        finally:
            await audit.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_sink_that_cannot_report_says_so_and_lets_the_node_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BCN_SERVER_TOKEN", "kana:secret")
    # case: no address at all
    for url in ("", None):
        audit = _sink(url)
        await audit.start(timeout=1)
        await audit.append(_event("event.lost", at_ms=1), timeout=1)
        assert "node.server.url" in str(audit.health["last_error"]), url
        assert audit.health["dropped"] == 1 and audit.health["queued"] == 0
        await audit.stop(timeout=1)

    # case: no token in the environment
    monkeypatch.delenv("BCN_SERVER_TOKEN")
    audit = _sink("http://127.0.0.1:1")
    await audit.start(timeout=1)
    await audit.append(_event("event.lost", at_ms=1), timeout=1)
    assert "BCN_SERVER_TOKEN" in str(audit.health["last_error"])
    assert audit.health["dropped"] == 1
    await audit.stop(timeout=1)
