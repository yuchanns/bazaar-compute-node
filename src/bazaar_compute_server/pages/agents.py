"""Agents: who is running where, and what each is doing."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed, sees
from ..activity import recent_lines, usage_today
from ..contacts import contacts
from ..control import Controls
from ..fleet import PAGE_SIZE, AgentPage, AgentView, agent_page, agent_view
from ..rendering import Renderer
from ..storage import IStorage


class AgentPages:
    def __init__(
        self, storage: IStorage, controls: Controls, renderer: Renderer
    ) -> None:
        self._storage = storage
        self._controls = controls
        self._render = renderer

    @allowed("agents.view")
    async def list(self, request: Request) -> Response:
        """The module with nothing open."""

        return self._page(
            request, await agent_page(self._storage, Access.of(request)), None
        )

    @allowed("agents.view")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def show(self, request: Request) -> Response:
        """The module, open on one agent."""

        params = request.path_params
        page, selected = await asyncio.gather(
            agent_page(self._storage, Access.of(request)),
            agent_view(
                self._storage,
                Access.of(request),
                params["computer_id"],
                params["agent_id"],
            ),
        )
        if selected is None:
            return HTMLResponse("", status_code=404)
        return self._page(request, page, selected)

    def _page(
        self, request: Request, page: AgentPage, selected: AgentView | None
    ) -> Response:
        return self._render.page(
            request,
            "agents",
            "agents.html",
            page=page,
            selected=selected,
            selected_key=(
                None if selected is None else f"{selected.computer_id}/{selected.id}"
            ),
        )

    @allowed("agents.view")
    async def list_fragment(self, request: Request) -> Response:
        """The list alone, for its own refresh, as far as `until`; or the rows
        of the page past `after`, for the scroll. `selected` names the open row."""

        query = request.query_params
        after = query.get("after") or None
        page = await agent_page(
            self._storage,
            Access.of(request),
            after=after,
            until=query.get("until") or None,
        )
        return self._render.fragment(
            request,
            "agent_rows.html" if after else "agent_list.html",
            page=page,
            selected_key=query.get("selected") or None,
        )

    @allowed("agents.view")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def activity_card(self, request: Request) -> Response:
        computer_id = request.path_params["computer_id"]
        agent_id = request.path_params["agent_id"]
        tz = self._render.zone(request)
        agent, activity, usage = await asyncio.gather(
            agent_view(self._storage, Access.of(request), computer_id, agent_id),
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

    @allowed("agents.view")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def contacts(self, request: Request) -> Response:
        """The agent's conversations: the column when it opens or retries, the
        rows past `offset` for the scroll, or the column again for a refresh
        past `since`, as far as `until` - which is nothing at all while no
        message has arrived or left since."""

        params = request.path_params
        query = request.query_params
        agent = await agent_view(
            self._storage, Access.of(request), params["computer_id"], params["agent_id"]
        )
        if agent is None:
            return HTMLResponse("", status_code=404)
        since = query.get("since")
        if since is not None:
            latest = await self._storage.latest_message_event(
                agent.computer_id, agent.id
            )
            if latest <= int(since):
                return Response(status_code=204)
        offset = int(query.get("offset") or 0)
        listing = await contacts(
            self._storage,
            self._controls,
            agent,
            offset=offset,
            limit=max(int(query.get("until") or 0), PAGE_SIZE),
        )
        return self._render.fragment(
            request,
            "contact_rows.html" if offset else "contacts.html",
            listing=listing,
        )


__all__ = ["AgentPages"]
