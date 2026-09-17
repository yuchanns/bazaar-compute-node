"""Computers: the enrolled ones, and enrolling a new one."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..fleet import fleet
from ..rendering import Renderer
from ..storage import IStorage


class ComputerPages:
    def __init__(self, storage: IStorage, renderer: Renderer) -> None:
        self._storage = storage
        self._render = renderer

    async def list(self, request: Request) -> Response:
        computers = await fleet(self._storage)
        selected_id = request.path_params.get("computer_id")
        selected = next(
            (item for item in computers if item.computer.id == selected_id),
            computers[0] if computers and selected_id is None else None,
        )
        if selected_id is not None and selected is None:
            return HTMLResponse("", status_code=404)
        return self._render.page(
            request,
            "computers",
            "computers.html",
            computers=computers,
            selected=selected,
            enrolment=None,
        )

    async def enrol_form(self, request: Request) -> Response:
        return self._render.fragment(request, "enrol_form.html")

    async def enrol(self, request: Request) -> Response:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            return await self.enrol_form(request)
        enrolment = await self._storage.add_computer(name)
        return self._render.page(
            request,
            "computers",
            "computers.html",
            computers=await fleet(self._storage),
            selected=None,
            enrolment=enrolment,
            base_url=str(request.base_url).rstrip("/"),
        )


__all__ = ["ComputerPages"]
