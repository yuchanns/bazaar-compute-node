"""One stateless check for all of a browser's current interests."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..access import Access
from ..activity import QUIET
from ..clock import day_text, now_ms
from ..fleet import ComputerView, agent_health, agent_page, computer_view, fleet
from ..polling import (
    CARD_QUIET,
    REMINDERS,
    PollRequest,
    activity_state,
    agent_info,
    changed,
    computer_detail,
    computer_info,
    computer_state,
    digest,
    health_state,
)
from ..refs import Refs
from ..rendering import Renderer
from ..storage import IStorage


class PollPages:
    def __init__(self, storage: IStorage, renderer: Renderer, refs: Refs) -> None:
        self._storage = storage
        self._render = renderer
        self.refs = refs

    async def check(self, request: Request) -> Response:
        try:
            body = PollRequest.model_validate_json(await request.body())
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        access = Access.of(request)
        shorts: set[str] = set()
        for subscription in body.subscriptions:
            scope = subscription.scope
            shorts.update(v for v in (scope.computer, scope.agent, scope.thread) if v)
            if scope.until:
                shorts.update(scope.until.split("/"))
        ordered = sorted(shorts)
        ids = dict(
            zip(ordered, await self.refs.expand([int(s) for s in ordered]), strict=True)
        )
        computers: dict[str, ComputerView | None] = {}
        states: dict[str, dict[str, Any] | None] = {}
        changes: list[dict[str, Any]] = []
        day = day_text(now_ms(), self._render.zone(request))
        # List snapshots also supply the rows, regardless of subscription order.
        for subscription in sorted(
            body.subscriptions, key=lambda item: item.scope.topic != "computers"
        ):
            scope = subscription.scope
            key = scope.model_dump_json()
            if key not in states:
                states[key] = None
                topic = scope.topic
                point = (
                    "computers.view" if topic.startswith("computer") else "agents.view"
                )
                if not access.allows(point):
                    continue
                if topic in ("agents", "computers"):
                    parts = (
                        [ids[s] for s in scope.until.split("/")] if scope.until else []
                    )
                    if None in parts:
                        continue
                    until = "/".join(p for p in parts if p is not None) or None
                    if topic == "agents":
                        page = await agent_page(self._storage, access, until=until)
                        states[key] = digest(
                            [[[a.computer_id, a.id] for a in page.agents], page.more]
                        )
                    else:
                        page = await fleet(self._storage, access, until=until)
                        computers.update(
                            (view.computer.id, view) for view in page.computers
                        )
                        states[key] = digest(
                            [[c.computer.id for c in page.computers], page.more]
                        )
                else:
                    computer = ids.get(scope.computer or "")
                    if computer is None:
                        continue
                    if computer not in computers:
                        computers[computer] = (
                            await computer_view(self._storage, access, computer)
                            if await access.can_see("computer", computer)
                            else None
                        )
                    view = computers[computer]
                    if view is None:
                        continue
                    if topic in ("computer", "computer-info", "computer-state"):
                        states[key] = {
                            "computer": computer_detail,
                            "computer-info": computer_info,
                            "computer-state": computer_state,
                        }[topic](view)
                    else:
                        agent = next(
                            (
                                a
                                for a in view.agents
                                if a.id == ids.get(scope.agent or "")
                            ),
                            None,
                        )
                        if agent is None:
                            continue
                        thread = ids.get(scope.thread or "")
                        if scope.thread is not None and thread is None:
                            continue
                        match topic:
                            case "agent-status":
                                states[key] = {"status": agent.status}
                            case "agent-info":
                                states[key] = agent_info(agent)
                            case "contacts" | "messages":
                                states[key] = {
                                    "since": await self._storage.latest_message_event(
                                        computer, agent.id, thread_id=thread
                                    ),
                                    "offline": agent.status == "offline",
                                }
                            case "events":
                                states[key] = {
                                    "since": await self._storage.latest_activity_event(
                                        computer, agent.id, skipping=QUIET
                                    )
                                }
                            case "activity":
                                since = await self._storage.latest_activity_event(
                                    computer, agent.id, skipping=CARD_QUIET
                                )
                                states[key] = activity_state(agent, since, day)
                            case "health":
                                since = await self._storage.latest_named_event(
                                    computer, agent.id, names=("usage.updated",)
                                )
                                health = await agent_health(
                                    self._storage, computer, agent.id
                                )
                                states[key] = health_state(health, agent, since, day)
                            case "reminders":
                                states[key] = {
                                    "since": await self._storage.latest_named_event(
                                        computer,
                                        agent.id,
                                        names=REMINDERS,
                                        thread_id=thread,
                                    ),
                                    "offline": agent.status == "offline",
                                }
        for subscription in body.subscriptions:
            state = states[subscription.scope.model_dump_json()]
            if state is None:
                changes.append({"id": subscription.id, "unavailable": True})
            elif changed(subscription.seen, state):
                item: dict[str, Any] = {"id": subscription.id, "state": state}
                if subscription.scope.topic in ("agent-status", "computer-state"):
                    item["data"] = state
                changes.append(item)
        return JSONResponse(
            {"changes": changes}, headers={"Cache-Control": "private, no-store"}
        )
