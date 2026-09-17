"""Agents: who is running where, and what each is doing."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..activity import recent_lines, usage_today
from ..fleet import agents_of, find_agent, fleet
from ..rendering import Renderer
from ..storage import IStorage


class AgentPages:
    def __init__(self, storage: IStorage, renderer: Renderer) -> None:
        self._storage = storage
        self._render = renderer

    async def list(self, request: Request) -> Response:
        params = request.path_params
        computers = await fleet(self._storage)
        selected = find_agent(
            computers, params.get("computer_id"), params.get("agent_id")
        )
        if selected is None and "agent_id" in params:
            return HTMLResponse("", status_code=404)
        return self._render.page(
            request,
            "agents",
            "agents.html",
            agents=agents_of(computers),
            selected=selected,
        )

    async def activity_card(self, request: Request) -> Response:
        computer_id = request.path_params["computer_id"]
        agent_id = request.path_params["agent_id"]
        tz = self._render.zone(request)
        computers, activity, usage = await asyncio.gather(
            fleet(self._storage),
            recent_lines(
                self._storage,
                self._render.translator(request),
                tz,
                computer_id,
                agent_id,
            ),
            usage_today(self._storage, tz, computer_id, agent_id),
        )
        agent = find_agent(computers, computer_id, agent_id)
        if agent is None:
            return HTMLResponse("", status_code=404)
        return self._render.fragment(
            request, "activity_card.html", agent=agent, activity=activity, usage=usage
        )


__all__ = ["AgentPages"]
