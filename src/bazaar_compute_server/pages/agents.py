"""Agents: who is running where, and what each is doing."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..activity import recent_lines, usage_today
from ..fleet import AgentView, agent_view, agents_of, fleet
from ..rendering import Renderer
from ..storage import IStorage


class AgentPages:
    def __init__(self, storage: IStorage, renderer: Renderer) -> None:
        self._storage = storage
        self._render = renderer

    async def list(self, request: Request) -> Response:
        params = request.path_params
        selected: AgentView | None = None
        if "agent_id" in params:
            selected = await agent_view(
                self._storage, params["computer_id"], params["agent_id"]
            )
            if selected is None:
                return HTMLResponse("", status_code=404)
        page = await fleet(self._storage)
        return self._render.page(
            request,
            "agents",
            "agents.html",
            fleet=page,
            agents=agents_of(page.computers),
            selected=selected,
            selected_key=(
                None if selected is None else f"{selected.computer_id}/{selected.id}"
            ),
        )

    async def list_fragment(self, request: Request) -> Response:
        """The list alone, for its own refresh, as far as `until`; or the rows
        of the page past `after`, for the scroll. `selected` names the open row."""

        query = request.query_params
        after = query.get("after") or None
        page = await fleet(self._storage, after=after, until=query.get("until") or None)
        return self._render.fragment(
            request,
            "agent_rows.html" if after else "agent_list.html",
            fleet=page,
            agents=agents_of(page.computers),
            selected_key=query.get("selected") or None,
        )

    async def activity_card(self, request: Request) -> Response:
        computer_id = request.path_params["computer_id"]
        agent_id = request.path_params["agent_id"]
        tz = self._render.zone(request)
        agent, activity, usage = await asyncio.gather(
            agent_view(self._storage, computer_id, agent_id),
            recent_lines(
                self._storage,
                self._render.translator(request),
                tz,
                computer_id,
                agent_id,
            ),
            usage_today(self._storage, tz, computer_id, agent_id),
        )
        if agent is None:
            return HTMLResponse("", status_code=404)
        return self._render.fragment(
            request, "activity_card.html", agent=agent, activity=activity, usage=usage
        )


__all__ = ["AgentPages"]
