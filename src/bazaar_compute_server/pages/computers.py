"""Computers: the enrolled ones, and enrolling a new one."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed, sees
from ..fleet import ComputerView, Fleet, computer_view, fleet
from ..protocol import MAX_NAME_CHARS
from ..refs import Refs
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
        return self._page(request, page, page.computers[0] if page.computers else None)

    @allowed("computers.view")
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
        return self._page(request, page, selected)

    def _page(
        self, request: Request, page: Fleet, selected: ComputerView | None
    ) -> Response:
        return self._render.page(
            request,
            "computers",
            "computers.html",
            fleet=page,
            selected=selected,
            selected_key=None if selected is None else selected.computer.id,
            enrolment=None,
        )

    @allowed("computers.view")
    async def list_fragment(self, request: Request) -> Response:
        """The list alone, for its own refresh, as far as `until`; or the rows
        of the page past `after`, for the scroll. `selected` names the open row."""

        query = request.query_params
        after = query.get("after") or None
        page = await fleet(
            self._storage,
            Access.of(request),
            after=after,
            until=query.get("until") or None,
        )
        return self._render.fragment(
            request,
            "computer_rows.html" if after else "computer_list.html",
            fleet=page,
            selected_key=query.get("selected") or None,
        )

    @allowed("computers.view")
    @sees("computer", "computer_id")
    async def detail(self, request: Request) -> Response:
        """One computer's pane alone, for its own refresh."""

        selected = await computer_view(
            self._storage, Access.of(request), request.path_params["computer_id"]
        )
        if selected is None:
            return HTMLResponse("", status_code=404)
        return self._render.fragment(request, "computer_detail.html", selected=selected)

    @allowed("computers.view")
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
    @sees("computer", "computer_id")
    async def remove_form(self, request: Request) -> Response:
        """The question before a computer is forgotten, in place of the button."""

        return self._render.fragment(
            request, "remove_form.html", computer_id=request.path_params["computer_id"]
        )

    @allowed("computers.delete")
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


def _system_of(request: Request) -> str:
    """Which kind of machine the browser is on, as it says itself: the
    client hint when there is one, else the user agent."""

    platform = request.headers.get("sec-ch-ua-platform") or request.headers.get(
        "user-agent", ""
    )
    return "windows" if "windows" in platform.lower() else "unix"


__all__ = ["ComputerPages"]
