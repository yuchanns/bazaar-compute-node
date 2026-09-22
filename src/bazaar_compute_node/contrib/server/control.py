"""Fetch requests from a bazaar compute server and answer them."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from inspect import isawaitable
from typing import Literal

import aiohttp
from pydantic import (
    BaseModel,
    ConfigDict,
    NonNegativeInt,
    PositiveInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
)
from yarl import URL

from ...core.actor import Agent
from ...core.audit import AuditEvent
from ...core.control import (
    AgentCommands,
    ControlContext,
    IControl,
    NodeCommands,
    Refused,
)
from ...core.correlation import CorrelationContext
from ...core.models import ReminderState, Review, RuntimeEventState
from ...core.reminder import ReminderListRequest
from ...core.serialize import (
    serialize_inbox_target,
    serialize_message,
    serialize_reminder,
)
from ...core.utils.clock import now_ms
from ...core.utils.text import format_exception
from .audit import BATCH_BYTES, PROTOCOL_VERSION


class _ContactsRead(BaseModel):
    """An agent's conversations, a page at a time, as its inbox has them."""

    model_config = ConfigDict(extra="ignore", strict=True)

    read: Literal["contacts"]
    agent_id: StrictStr
    limit: PositiveInt = 50
    offset: NonNegativeInt = 0
    # the conversations in one review state; none given lists them all
    review: Literal["pending", "approved", "denied"] | None = "approved"

    async def answer(self, commands: AgentCommands) -> dict[str, object]:
        # the whole agent is in reach of whoever looks at it from outside
        result = await commands.service.check_inbox(
            Agent(self.agent_id),
            limit=self.limit,
            offset=self.offset,
            pending_only=False,
            review=None if self.review is None else Review(self.review),
        )
        return {
            "targets": [
                {
                    **serialize_inbox_target(target),
                    # who answers for the conversation is the node's mode to
                    # know; a read of it is asked as that actor
                    "actor_id": commands.actors.for_thread(target.thread_id).id,
                }
                for target in result.targets
            ],
            "total": result.total,
            "shown": result.shown,
            "offset": result.offset,
            "has_more": result.has_more,
            "pending_review": result.pending_review,
        }


class _HistoryRead(BaseModel):
    """One conversation's messages around one of them, read as the actor the
    conversation is answered by."""

    model_config = ConfigDict(extra="ignore", strict=True)

    read: Literal["history"]
    agent_id: StrictStr
    actor_id: StrictStr
    target: StrictStr
    around_message_id: StrictStr | None = None
    limit: PositiveInt = 100

    async def answer(self, commands: AgentCommands) -> dict[str, object]:
        # whoever looks from outside reads a conversation in any review state
        result = await commands.service.read_messages(
            commands.actors.resolve(self.actor_id),
            raw_target=self.target,
            around_message_id=self.around_message_id,
            limit=self.limit,
            review=None,
        )
        return {
            "messages": [
                serialize_message(message, result.target_projections)
                for message in result.messages
            ],
            "referenced_messages": [
                serialize_message(message, result.target_projections)
                for message in result.referenced_messages
            ],
            "snapshot_seq": result.snapshot_seq,
            "first_seq": result.first_seq,
            "last_seq": result.last_seq,
            "has_before": result.has_before,
        }


class _RemindersRead(BaseModel):
    """The reminders still to come that an actor can reach."""

    model_config = ConfigDict(extra="ignore", strict=True)

    read: Literal["reminders"]
    agent_id: StrictStr
    actor_id: StrictStr

    async def answer(self, commands: AgentCommands) -> dict[str, object]:
        result = await commands.service.list_reminders(
            commands.actors.resolve(self.actor_id),
            ReminderListRequest(statuses=frozenset({ReminderState.SCHEDULED})),
        )
        return {
            "reminders": [serialize_reminder(reminder) for reminder in result.reminders]
        }


class _SettingRead(BaseModel):
    """What the agent is set to do under a key - read from where it is kept,
    so an agent not running says it too, the way it is written."""

    model_config = ConfigDict(extra="ignore", strict=True)

    read: Literal["setting"]
    agent_id: StrictStr
    key: Literal["review.reply"]

    async def answer(self, node: NodeCommands) -> dict[str, object]:
        return {
            "key": self.key,
            "value": await node.read_setting(self.agent_id, self.key),
        }


class _ReviewWrite(BaseModel):
    """Whether whoever is behind a conversation may talk to the agent."""

    model_config = ConfigDict(extra="ignore", strict=True)

    write: Literal["review"]
    agent_id: StrictStr
    thread_id: StrictStr
    review: Literal["approved", "denied"]

    async def answer(self, commands: AgentCommands) -> dict[str, object]:
        session = await commands.service.review_contact(
            self.thread_id, Review(self.review)
        )
        return {"thread_id": self.thread_id, "review": session.review.value}


class _SettingWrite(BaseModel):
    """Set what the agent does under a key."""

    model_config = ConfigDict(extra="ignore", strict=True)

    write: Literal["setting"]
    agent_id: StrictStr
    key: Literal["review.reply"]
    value: StrictStr

    async def answer(self, commands: AgentCommands) -> dict[str, object]:
        await commands.service.set_setting(self.key, self.value)
        return {"key": self.key, "value": self.value}


class _AgentsRead(BaseModel):
    """Every agent the node runs, and what it could run."""

    model_config = ConfigDict(extra="ignore", strict=True)

    read: Literal["agents"]

    def answer(self, node: NodeCommands) -> Mapping[str, object]:
        return node.read_agents()


class _AgentWrite(BaseModel):
    """One agent taken in or changed. The agent itself crosses as the
    mapping the configuration file holds it in, which only the node reads;
    credentials come beside it, by channel and by option, and so do the
    values of a runtime's environment, by runtime and by name; neither is
    ever written into the configuration."""

    model_config = ConfigDict(extra="ignore", strict=True)

    write: Literal["agent"]
    agent: dict[str, object]
    secrets: dict[StrictStr, dict[StrictStr, StrictStr]] = {}
    env: dict[StrictStr, dict[StrictStr, StrictStr]] = {}
    # what the agent does, kept with it as it is written
    settings: dict[Literal["review.reply"], StrictStr] = {}

    async def answer(self, node: NodeCommands) -> Mapping[str, object]:
        settings = {str(key): value for key, value in self.settings.items()}
        return await node.write_agent(self.agent, self.secrets, self.env, settings)


class _AgentRemove(BaseModel):
    """One agent let go."""

    model_config = ConfigDict(extra="ignore", strict=True)

    remove: Literal["agent"]
    agent_id: StrictStr

    async def answer(self, node: NodeCommands) -> Mapping[str, object]:
        return await node.delete_agent(self.agent_id)


class _WorkspaceRead(BaseModel):
    """What one agent has in its workspace, a directory at a time: the top,
    or the one at a path relative to it."""

    model_config = ConfigDict(extra="ignore", strict=True)

    read: Literal["workspace"]
    agent_id: StrictStr
    path: StrictStr = ""

    async def answer(self, node: NodeCommands) -> Mapping[str, object]:
        return await node.read_workspace(self.agent_id, self.path)


# what a server may ask a node: the same services the runtime's own `bcc`
# reaches through the local command server, without the caller checks that
# server does for a runtime, since the caller here is the node - and what
# only an operator may do, which `bcc` has no word for. each request knows
# how it is answered; `read` or `write` tells them apart on the wire
type _AgentRequest = (
    _ContactsRead | _HistoryRead | _RemindersRead | _ReviewWrite | _SettingWrite
)
# and what it may ask about the node rather than of one agent: the
# operator's own requests, which `bcc` has no word for
type _NodeRequest = (
    _AgentsRead | _SettingRead | _AgentWrite | _AgentRemove | _WorkspaceRead
)
type _Request = _AgentRequest | _NodeRequest
_REQUESTS: TypeAdapter[_Request] = TypeAdapter(_Request)

# how long the server may hold a getUpdates before answering with nothing,
# and how much longer than that the node waits for it
POLL_SECONDS = 25
POLL_GRACE_SECONDS = 5
# a reply to getUpdates is a few requests of the command protocol
UPDATES_BYTES = 64 * 1024


class ServerControl(IControl):
    """Long-poll the server for requests, run each through the node, and
    answer through the sink as `control.result` events. A server that is
    not there is asked again later, quietly; the node is never held up."""

    @property
    def name(self) -> str:
        return "server"

    def __init__(self, context: ControlContext) -> None:
        url = context.options.get("url")
        self._endpoint = (
            URL(url) / "node" / "getUpdates" if isinstance(url, str) and url else None
        )
        token_env = context.options.get("token_env")
        self._token = os.environ.get(token_env) if isinstance(token_env, str) else None
        self._unusable: str | None = None
        if self._endpoint is None:
            self._unusable = (
                f"node.server.url is not set: {context.options.get('url')!r}"
            )
        elif not self._token:
            self._unusable = f"environment variable {token_env!r} is not set"
        self._audit = context.audit
        self._commands_of = context.commands_of
        self._node = context.node
        self._timer_wheel = context.timer_wheel
        # a fetch that fails waits this long before the next: as long as the
        # node is allowed to be silent
        self._backoff_ms = math.ceil(context.timeout_budget.startup_seconds * 1_000)
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._offset = 0
        self._served = 0
        self._last_error: str | None = self._unusable
        self._last_polled_at_ms: int | None = None
        self._logger = logging.getLogger("bazaar_compute_node.control.server")

    @property
    def health(self) -> Mapping[str, object]:
        return {
            "served": self._served,
            "last_error": self._last_error,
            "last_polled_at_ms": self._last_polled_at_ms,
        }

    async def start(self, *, timeout: float) -> None:
        del timeout
        if self._task is not None:
            return
        if self._unusable is not None:
            self._logger.warning(
                "not taking requests from the server: %s", self._unusable
            )
            return
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-BCS-Protocol": str(PROTOCOL_VERSION),
            }
        )
        self._task = asyncio.create_task(self._fetch(), name="bcn-server-control")

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

    async def _fetch(self) -> None:
        while True:
            updates = await self._updates()
            if updates is None:
                await self._timer_wheel.create(self._backoff_ms).wait()
                continue
            for update in updates:
                await self._answer(update)

    async def _updates(self) -> list[Mapping[str, object]] | None:
        """One long poll; nothing when the server did not answer properly,
        with the reason in the health."""

        session = self._session
        if session is None or self._endpoint is None:
            return None
        self._last_polled_at_ms = now_ms()
        try:
            async with session.post(
                self._endpoint,
                data=_encode({"offset": self._offset}),
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=POLL_SECONDS + POLL_GRACE_SECONDS),
            ) as response:
                body = await _read(response, UPDATES_BYTES)
                if response.status != 200:
                    self._last_error = f"http {response.status}"
                    return None
                reply = json.loads(body)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            self._last_error = f"{type(error).__name__}: {error}"
            return None
        if not isinstance(reply, Mapping) or reply.get("ok") is not True:
            code = reply.get("error_code") if isinstance(reply, Mapping) else None
            self._last_error = str(code or "malformed reply")
            return None
        result = reply.get("result")
        updates = result.get("updates") if isinstance(result, Mapping) else None
        if not isinstance(updates, list):
            self._last_error = "malformed reply"
            return None
        self._last_error = None
        return [update for update in updates if isinstance(update, Mapping)]

    async def _answer(self, update: Mapping[str, object]) -> None:
        update_id = update.get("update_id")
        request = update.get("request")
        if not isinstance(update_id, int) or not isinstance(request, Mapping):
            self._last_error = "malformed update"
            return
        # the answer rides the sink like any event, as text: it is the
        # server's to read, not the audit's to look into; one that would not
        # fit a report is answered with why instead
        response = _encode(await self._answer_request(request))
        # sized as the event will carry it: text inside JSON, escaped again
        if len(_encode(response)) > BATCH_BYTES // 2:
            response = _encode(
                {
                    "ok": False,
                    "code": "RESULT_TOO_LARGE",
                    "error": "the response is larger than a report may carry",
                }
            )
        agent_id = request.get("agent_id")
        await self._audit.append(
            AuditEvent(
                event_name="control.result",
                state=RuntimeEventState.COMPLETED,
                created_at_ms=now_ms(),
                correlation=CorrelationContext(
                    node_id=agent_id if isinstance(agent_id, str) else None
                ),
                metadata={"update_id": update_id, "response": response},
            ),
            timeout=0,
        )
        self._offset = max(self._offset, update_id)
        self._served += 1

    def _answer_of(
        self, read: _Request
    ) -> Callable[[], Mapping[str, object] | Awaitable[Mapping[str, object]]] | None:
        """How a request is answered: about the node by the node, to an
        agent by that agent's commands - nothing when it is not running."""

        if isinstance(
            read,
            _AgentsRead | _SettingRead | _AgentWrite | _AgentRemove | _WorkspaceRead,
        ):
            return partial(read.answer, self._node)
        commands = self._commands_of(read.agent_id)
        return None if commands is None else partial(read.answer, commands)

    async def _answer_request(
        self, request: Mapping[str, object]
    ) -> Mapping[str, object]:
        try:
            read = _REQUESTS.validate_python(request)
        except ValidationError as error:
            return {"ok": False, "code": "INVALID_REQUEST", "error": str(error)}
        answer = self._answer_of(read)
        if answer is None:
            return {
                "ok": False,
                "code": "AGENT_NOT_AVAILABLE",
                "error": "Agent is not available",
            }
        try:
            result = answer()
            return {
                "ok": True,
                "result": await result if isawaitable(result) else result,
            }
        except Refused as error:
            return {"ok": False, "code": "REFUSED", "error": str(error)}
        except ValueError as error:
            return {"ok": False, "code": "TARGET_NOT_FOUND", "error": str(error)}
        except Exception as error:
            # a request that failed is answered so, the way the local command
            # server answers a caller; the poll goes on
            self._logger.exception("request failed", extra={"request": dict(request)})
            return {
                "ok": False,
                "code": "REQUEST_FAILED",
                "error": format_exception(error),
            }


def _encode(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


async def _read(response: aiohttp.ClientResponse, limit: int) -> bytes:
    """The body up to the limit, whole however the network splits it;
    whatever comes past the limit is not read."""

    body = b""
    while len(body) < limit:
        chunk = await response.content.read(limit - len(body))
        if not chunk:
            break
        body += chunk
    return body


__all__ = ["POLL_SECONDS", "ServerControl"]
