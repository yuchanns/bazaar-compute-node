"""One stateless check for all of a browser's current interests."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..access import Access
from ..clock import day_text, now_ms
from ..poll_rendering import changed
from ..polling import TOPICS, PollContext, PollRequest
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
        context = PollContext(
            self._storage, access, ids, day_text(now_ms(), self._render.zone(request))
        )
        states: dict[str, dict[str, Any] | None] = {}
        changes: list[dict[str, Any]] = []
        for subscription in sorted(
            body.subscriptions, key=lambda item: TOPICS[item.scope.topic].priority
        ):
            scope = subscription.scope
            key = scope.model_dump_json()
            if key in states:
                continue
            states[key] = None
            topic = TOPICS[scope.topic]
            refs = [
                value for value in (scope.computer, scope.agent, scope.thread) if value
            ]
            if scope.until:
                refs.extend(scope.until.split("/"))
            if any(ids[ref] is None for ref in refs):
                continue
            if await topic.permission(
                access,
                {
                    "computer_id": ids.get(scope.computer or ""),
                    "agent_id": ids.get(scope.agent or ""),
                },
            ):
                states[key] = await topic.fetch(context, scope)
        for subscription in body.subscriptions:
            state = states[subscription.scope.model_dump_json()]
            if state is None:
                changes.append({"id": subscription.id, "unavailable": True})
            elif changed(subscription.seen, state):
                item: dict[str, Any] = {"id": subscription.id, "state": state}
                if TOPICS[subscription.scope.topic].inline:
                    item["data"] = state
                changes.append(item)
        return JSONResponse(
            {"changes": changes}, headers={"Cache-Control": "private, no-store"}
        )
