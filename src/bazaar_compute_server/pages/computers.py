"""Computers: the enrolled ones, and enrolling a new one."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed, sees
from ..fleet import ComputerView, Fleet, computer_view, fleet
from ..protocol import MAX_NAME_CHARS
from ..refs import Refs, expanded
from ..rendering import Renderer
from ..storage import IStorage


class ComputerPages:
    def __init__(self, storage: IStorage, renderer: Renderer, refs: Refs) -> None:
        self._storage = storage
        self._render = renderer
        self.refs = refs

    @allowed("computers.view")
    async def list(self, request: Request) -> Response:
        """The module, open on the first computer when there is one."""

        page = await fleet(self._storage, Access.of(request))
        return await self._page(
            request, page, page.computers[0] if page.computers else None
        )

    @allowed("computers.view")
    @expanded("computer_id")
    @sees("computer", "computer_id")
    async def show(self, request: Request) -> Response:
        """The module, open on one computer."""

        page, selected = await asyncio.gather(
            fleet(self._storage, Access.of(request)),
            computer_view(
                self._storage, Access.of(request), request.path_params["computer_id"]
            ),
        )
        if selected is None:
            return HTMLResponse("", status_code=404)
        return await self._page(request, page, selected)

    async def _id(self, short: str | None) -> str | None:
        """The id behind a number a link carries; nothing for none, or one
        that stands for nothing."""

        if not short:
            return None
        ids = await self.refs.values([short])
        return None if ids is None else ids[0]

    async def _page(
        self, request: Request, page: Fleet, selected: ComputerView | None
    ) -> Response:
        await self.refs.load(_named(page.computers, selected))
        return self._render.page(
            request,
            "computers",
            "computers.html",
            fleet=page,
            selected=selected,
            # the key a refresh brings back is text; the row compares as text
            selected_key=None
            if selected is None
            else str(self.refs.ref(selected.computer.id)),
            enrolment=None,
        )

    @allowed("computers.view")
    async def list_fragment(self, request: Request) -> Response:
        """The list alone, for its own refresh, as far as `until`; or the rows
        of the page past `after`, for the scroll. `selected` names the open row."""

        query = request.query_params
        after, until = await asyncio.gather(
            self._id(query.get("after")), self._id(query.get("until"))
        )
        page = await fleet(self._storage, Access.of(request), after=after, until=until)
        await self.refs.load(_named(page.computers))
        return self._render.fragment(
            request,
            "computer_rows.html" if after else "computer_list.html",
            fleet=page,
            selected_key=query.get("selected") or None,
        )

    @allowed("computers.view")
    @expanded("computer_id")
    @sees("computer", "computer_id")
    async def detail(self, request: Request) -> Response:
        """One computer's pane alone, for its own refresh."""

        selected = await computer_view(
            self._storage, Access.of(request), request.path_params["computer_id"]
        )
        if selected is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load(_named((), selected))
        return self._render.fragment(request, "computer_detail.html", selected=selected)

    @allowed("computers.view")
    @expanded("computer_id")
    @sees("computer", "computer_id")
    async def presence(self, request: Request) -> Response:
        """Whether a computer has shown up yet; polled while it has not."""

        item = await computer_view(
            self._storage, Access.of(request), request.path_params["computer_id"]
        )
        if item is None:
            return HTMLResponse("", status_code=404)
        return self._render.fragment(request, "presence.html", item=item)

    @allowed("computers.delete")
    @expanded("computer_id")
    @sees("computer", "computer_id")
    async def remove_form(self, request: Request) -> Response:
        """The question before a computer is forgotten, in place of the button."""

        return self._render.fragment(
            request, "remove_form.html", computer_id=request.path_params["computer_id"]
        )

    @allowed("computers.delete")
    @expanded("computer_id")
    @sees("computer", "computer_id")
    async def remove(self, request: Request) -> Response:
        if not await self._storage.remove_computer(request.path_params["computer_id"]):
            return HTMLResponse("", status_code=404)
        response = await self.list(request)
        response.headers["HX-Push-Url"] = "/computers"
        return response

    @allowed("computers.create")
    async def enrol_form(self, request: Request) -> Response:
        return self._render.fragment(request, "enrol_form.html")

    @allowed("computers.create")
    async def enrol(self, request: Request) -> Response:
        form = await request.form()
        # a name is cut to what a row shows, like the names nodes report
        name = str(form.get("name", "")).strip()[:MAX_NAME_CHARS]
        if not name:
            return await self.enrol_form(request)
        enrolment = await self._storage.add_computer(
            name, owner_id=Access.of(request).account.id
        )
        page, item = await asyncio.gather(
            fleet(self._storage, Access.of(request)),
            computer_view(self._storage, Access.of(request), enrolment.computer.id),
        )
        await self.refs.load(_named(page.computers, item))
        return self._render.page(
            request,
            "computers",
            "computers.html",
            fleet=page,
            selected=None,
            selected_key=None,
            enrolment=enrolment,
            item=item,
            base_url=str(request.base_url).rstrip("/"),
            system=_system_of(request),
        )


def _named(
    computers: Iterable[ComputerView], selected: ComputerView | None = None
) -> list[str]:
    """What a page names in a link: the computers, and the agents of the one
    it is open on."""

    named = [item.computer.id for item in computers]
    if selected is not None:
        named.append(selected.computer.id)
        named.extend(agent.id for agent in selected.agents)
    return named


def _system_of(request: Request) -> str:
    """Which kind of machine the browser is on, as it says itself: the
    client hint when there is one, else the user agent."""

    platform = request.headers.get("sec-ch-ua-platform") or request.headers.get(
        "user-agent", ""
    )
    return "windows" if "windows" in platform.lower() else "unix"


__all__ = ["ComputerPages"]
