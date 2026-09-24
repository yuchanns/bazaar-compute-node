"""An agent's own page: what it is made of, changed and let go from here;
what it found to do, what it has to hand, how it is doing, what it did."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed, sees
from ..activity import PAGE_EVENTS, event_lines, usage_today
from ..clock import day_text, now_ms
from ..configure import (
    SECRETS,
    configuration,
    held,
    remove,
    reply_of,
    save,
    skills,
    workspace,
)
from ..control import Controls
from ..fleet import AgentView, agent_health, agent_page, agent_view
from ..refs import Refs, expanded
from ..rendering import Renderer
from ..storage import IStorage
from .agents import AgentPages, named

# the page's tabs, in the order they stand; the first is where it opens
TABS = ("config", "skills", "workspace", "status", "activity")


class ProfilePages:
    def __init__(
        self,
        storage: IStorage,
        controls: Controls,
        renderer: Renderer,
        refs: Refs,
        agents: AgentPages,
    ) -> None:
        self._storage = storage
        self._controls = controls
        self._render = renderer
        self.refs = refs
        self._agents = agents

    @allowed("agents.view")
    @expanded("computer_id", "agent_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def show(self, request: Request) -> Response:
        """The agents module, open on one agent's page at one tab."""

        tab = request.query_params.get("tab", TABS[0])
        if tab not in TABS:
            return HTMLResponse("", status_code=404)
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
        await self.refs.load(named([*page.agents, selected]))
        return self._render.page(
            request,
            "agents",
            "agents.html",
            page=page,
            selected=selected,
            selected_key=f"{self.refs.ref(selected.computer_id)}/{self.refs.ref(selected.id)}",
            contact=None,
            latest=None,
            asked=None,
            profile=tab,
            **await self._tab(request, selected, tab),
        )

    @allowed("agents.view")
    @expanded("computer_id", "agent_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def tab(self, request: Request) -> Response:
        """One tab's content alone, for switching tabs."""

        tab = request.path_params["tab"]
        if tab not in TABS:
            return HTMLResponse("", status_code=404)
        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
        return self._render.fragment(
            request,
            f"agent_profile_{tab}.html",
            selected=selected,
            **await self._tab(request, selected, tab),
        )

    @allowed("agents.update")
    @expanded("computer_id", "agent_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def save(self, request: Request) -> Response:
        """The agent as the page has it, written to its computer; the tab
        comes back as the computer now holds it, or as it was filled in
        with why not."""

        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
        agents = await held(
            self._controls,
            selected.computer_id,
            online=selected.status != "offline",
        )
        kept = agents.agent(selected.id)
        if kept is None:
            return self._render.fragment(
                request,
                "agent_profile_config.html",
                selected=selected,
                held=agents,
                values=None,
                reply="",
                failed=None,
                saved=False,
                secrets=SECRETS,
            )
        form = await request.form()
        reply = str(form.get("reply", "")).strip()
        agent, secrets, env, origins = configuration(
            {key: str(value) for key, value in form.items()}, kept
        )
        failed = await save(self._controls, selected, agent, secrets, env, reply)
        if failed is None:
            # read back: what the computer holds now is what the tab shows
            agents = await held(
                self._controls,
                selected.computer_id,
                online=selected.status != "offline",
            )
            values = agents.agent(selected.id) or kept
            response = self._render.fragment(
                request,
                "agent_profile_config.html",
                selected=selected,
                values=values,
                # the page's heading as the computer now holds the agent,
                # before its next beat says so
                head=replace(
                    selected,
                    name=values["name"],
                    channels=tuple(card["kind"] for card in values["channel"]),
                    runtimes=tuple(card["kind"] for card in values["runtime"]),
                ),
                reply=reply,
                failed=None,
                saved=True,
                secrets=SECRETS,
            )
            response.headers["HX-Trigger"] = "agents-changed"
            return response
        word, _, code = failed.partition(":")
        if word == "refused" and code in {"REFUSED", "INVALID_REQUEST"}:
            failed = f"invalid:{code}"
        return self._render.fragment(
            request,
            "agent_profile_config.html",
            selected=selected,
            values={**agent, "id": selected.id, "secrets": kept["secrets"]},
            origins=origins,
            reply=reply,
            failed=failed,
            saved=False,
            secrets=SECRETS,
        )

    @allowed("agents.delete")
    @expanded("computer_id", "agent_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def remove_ask(self, request: Request) -> Response:
        """The question before an agent is let go, over the page."""

        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
        return self._render.fragment(
            request, "agent_remove.html", selected=selected, failed=None
        )

    @allowed("agents.delete")
    @expanded("computer_id", "agent_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def remove(self, request: Request) -> Response:
        """The agent let go from its computer: back to the module with
        nothing open; or the question stays up with why not."""

        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
        failed = await remove(self._controls, selected)
        if failed is not None:
            response = self._render.fragment(
                request, "agent_remove.html", selected=selected, failed=failed
            )
            response.headers["HX-Retarget"] = "#agent-remove"
            response.headers["HX-Reswap"] = "outerHTML"
            return response
        response = await self._agents.list(request)
        response.headers["HX-Push-Url"] = "/agents"
        return response

    @allowed("agents.view")
    @expanded("computer_id", "agent_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def events(self, request: Request) -> Response:
        """The agent's events past one: older ones for the scroll, newer
        ones for the refresh at the top. Each comes with the day of the line
        above it, so a day starting here is marked; a refresh also with the
        day below it, when that day is not marked yet."""

        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
        query = request.query_params
        before = query.get("before")
        after = query.get("after")
        lines = await event_lines(
            self._storage,
            self._render.translator(request),
            selected.computer_id,
            selected.id,
            before=int(before) if before else None,
            after=int(after) if after else None,
        )
        return self._render.fragment(
            request,
            "agent_profile_events.html",
            selected=selected,
            lines=lines,
            above=query.get("above") or self._today(request),
            below=query.get("below") or None,
            fresh=after is not None,
            # a refresh for newer ones has no end to scroll past
            more=after is None and len(lines) == PAGE_EVENTS,
        )

    async def _selected(self, request: Request) -> AgentView | None:
        params = request.path_params
        selected = await agent_view(
            self._storage,
            Access.of(request),
            params["computer_id"],
            params["agent_id"],
        )
        if selected is not None:
            await self.refs.load(named([selected]))
        return selected

    async def _tab(
        self, request: Request, selected: AgentView, tab: str
    ) -> dict[str, Any]:
        """What one tab shows."""

        if tab == "config":
            agents, reply = await asyncio.gather(
                held(
                    self._controls,
                    selected.computer_id,
                    online=selected.status != "offline",
                ),
                reply_of(self._controls, selected),
            )
            return {
                "held": agents,
                "values": agents.agent(selected.id),
                "reply": reply or "",
                "failed": None,
                "saved": False,
                "secrets": SECRETS,
            }
        if tab == "skills":
            return {"skills": await skills(self._controls, selected)}
        if tab == "workspace":
            # the top of it, or with a path the directory there alone
            return {
                "workspace": await workspace(
                    self._controls, selected, request.query_params.get("path", "")
                )
            }
        if tab == "status":
            health, usage = await asyncio.gather(
                agent_health(self._storage, selected.computer_id, selected.id),
                usage_today(
                    self._storage,
                    self._render.zone(request),
                    selected.computer_id,
                    selected.id,
                ),
            )
            return {"health": health, "usage": usage}
        lines = await event_lines(
            self._storage,
            self._render.translator(request),
            selected.computer_id,
            selected.id,
        )
        return {
            "lines": lines,
            "above": self._today(request),
            "below": None,
            "fresh": False,
            "more": len(lines) == PAGE_EVENTS,
        }

    def _today(self, request: Request) -> str:
        """Today's day: the one the newest lines need not be marked with."""

        return day_text(now_ms(), self._render.zone(request))


__all__ = ["TABS", "ProfilePages"]
