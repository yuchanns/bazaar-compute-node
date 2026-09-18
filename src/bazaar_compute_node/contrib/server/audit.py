"""Send the audit stream to a bazaar compute server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from uuid import uuid7

import aiohttp

from ...core.audit import AuditEvent
from ...core.lifecycle import TimeoutBudget
from ...core.observability import IAudit
from ...core.utils.clock import now_ms

PROTOCOL_VERSION = 1
# half of the server's request limit (1 MiB); a backlog goes over in requests
# of this size instead of one request that is too big to ever get through
BATCH_BYTES = 512 * 1024


def endpoint(url: object) -> str | None:
    """Where reports go for a configured address, or nothing when there is
    no address. What the address is worth shows when a report is sent."""

    if not isinstance(url, str) or not url:
        return None
    return f"{url.rstrip('/')}/node/reportEvents"


class ServerAudit(IAudit):
    """Queue events as they come and post them in batches, in order. A batch
    is sent once: the server has it or it is dropped, the node never waits on
    it and never holds it back. What is wrong with the setup shows in
    `health`, it does not stop the node."""

    @property
    def name(self) -> str:
        return "server"

    def __init__(
        self,
        options: Mapping[str, object],
        *,
        timeout_budget: TimeoutBudget,
    ) -> None:
        # one request gets what one command gets
        self._request_timeout = timeout_budget.command_seconds
        self._endpoint = endpoint(options.get("url"))
        token_env = options.get("token_env")
        self._token = os.environ.get(token_env) if isinstance(token_env, str) else None
        self._unusable: str | None = None
        if self._endpoint is None:
            self._unusable = f"node.server.url is not an absolute http(s) URL: {options.get('url')!r}"
        elif not self._token:
            self._unusable = f"environment variable {token_env!r} is not set"
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        # what is waiting, in bytes: while a report is out, the queue holds
        # at most the next request's worth; past that, events are let go at
        # the door rather than piling up behind a server that will not answer
        self._queued_bytes = 0
        self._seq = 0
        self._run_id = str(uuid7())
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._carry: str | None = None
        self._holding = 0
        self._sent = 0
        self._dropped = 0
        self._last_error: str | None = self._unusable
        self._last_sent_at_ms: int | None = None
        self._logger = logging.getLogger("bazaar_compute_node.audit.server")

    @property
    def health(self) -> Mapping[str, object]:
        return {
            "run_id": self._run_id,
            "queued": self._queue.qsize()
            + self._holding
            + (0 if self._carry is None else 1),
            "sent": self._sent,
            "dropped": self._dropped,
            "last_error": self._last_error,
            "last_sent_at_ms": self._last_sent_at_ms,
        }

    async def start(self, *, timeout: float) -> None:
        del timeout
        if self._task is not None:
            return
        if self._unusable is not None:
            self._logger.warning("not reporting to the server: %s", self._unusable)
            return
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-BCS-Protocol": str(PROTOCOL_VERSION),
            }
        )
        self._task = asyncio.create_task(self._run(), name="bcn-server-audit")

    async def stop(self, *, timeout: float) -> None:
        del timeout
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        session = self._session
        self._session = None
        if session is not None:
            await session.close()

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        del timeout
        if self._unusable is not None:
            self._dropped += 1
            return
        self._seq += 1
        # encoded here, once, with the same policy the sizing and the request
        # use: nothing on the way out can then fail to serialise
        encoded = _encode({"seq": self._seq, **event.as_payload()})
        if len(encoded) > BATCH_BYTES:
            self._dropped += 1
            self._last_error = f"event {self._seq} is larger than a request; dropped"
            return
        if self._queued_bytes + len(encoded) > BATCH_BYTES:
            self._dropped += 1
            self._last_error = f"queue full: event {self._seq} dropped"
            return
        self._queued_bytes += len(encoded)
        self._queue.put_nowait(encoded)

    async def _run(self) -> None:
        while True:
            batch = await self._next_batch()
            self._holding = len(batch)
            if await self._report(batch):
                self._sent += len(batch)
            else:
                self._dropped += len(batch)
            self._holding = 0

    async def _next_batch(self) -> list[str]:
        """Take what is queued, in order, up to one request's worth."""

        first = self._carry or await self._take()
        self._carry = None
        batch = [first]
        size = len(first)
        while not self._queue.empty():
            event = self._queue.get_nowait()
            self._queued_bytes -= len(event)
            size += len(event)
            if size > BATCH_BYTES:
                # the one that does not fit leads the next batch
                self._carry = event
                break
            batch.append(event)
        return batch

    async def _take(self) -> str:
        event = await self._queue.get()
        self._queued_bytes -= len(event)
        return event

    async def _report(self, batch: list[str]) -> bool:
        """Post one batch; false when it did not get through, with the reason
        in `last_error`."""

        session = self._session
        if session is None:
            return False
        try:
            async with session.post(
                self._endpoint or "",
                data=f'{{"run_id":{_encode(self._run_id)},"events":[{",".join(batch)}]}}',
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=self._request_timeout),
            ) as response:
                # a reply is a few short fields, read whole however the
                # network splits it; whatever comes past that is not read
                body = b""
                while len(body) < _REPLY_BYTES:
                    chunk = await response.content.read(_REPLY_BYTES - len(body))
                    if not chunk:
                        break
                    body += chunk
                if response.status != 200:
                    # the server says why in its envelope, when it is the
                    # server answering and not something in front of it
                    self._last_error = (
                        f"http {response.status} {_error_code(body)}:"
                        f" {len(batch)} events dropped"
                    )
                    return False
                reply = json.loads(body)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            self._last_error = f"{type(error).__name__}: {error}"
            return False
        if not isinstance(reply, Mapping) or reply.get("ok") is not True:
            code = reply.get("error_code") if isinstance(reply, Mapping) else None
            self._last_error = str(code or "malformed reply")
            return False
        self._last_error = None
        self._last_sent_at_ms = now_ms()
        return True


def _encode(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


_REPLY_BYTES = 4096


def _error_code(body: bytes) -> str:
    """The error code in a server's envelope, or what kind of body it was."""

    try:
        reply = json.loads(body)
    except ValueError:
        return "unexpected body"
    code = reply.get("error_code") if isinstance(reply, Mapping) else None
    return str(code) if code else "no error code"


__all__ = ["BATCH_BYTES", "PROTOCOL_VERSION", "ServerAudit", "endpoint"]
