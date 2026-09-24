"""Computers: the enrolled ones, enrolling a new one, and taking an agent in
on one."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed, sees
from ..configure import SECRETS, configuration, create, kinds, models
from ..control import Controls
from ..fleet import ComputerView, Fleet, computer_view, fleet
from ..protocol import MAX_NAME_CHARS
from ..refs import Refs, expanded
from ..rendering import Renderer
from ..storage import IStorage


class ComputerPages:
    def __init__(
        self, storage: IStorage, controls: Controls, renderer: Renderer, refs: Refs
    ) -> None:
        self._storage = storage
        self._controls = controls
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

        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
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

    @allowed("agents.create")
    @expanded("computer_id")
    @sees("computer", "computer_id")
    async def new_agent(self, request: Request) -> Response:
        """The form for a new agent on one computer, over its page. The
        computer is not asked anything for it: what it can run is asked for
        where the form needs it."""

        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
        return self._render.fragment(
            request,
            "agent_new.html",
            selected=selected,
            values={},
            reply="",
            failed=None,
            secrets=SECRETS,
        )

    @allowed("agents.create")
    @expanded("computer_id")
    @sees("computer", "computer_id")
    async def create_agent(self, request: Request) -> Response:
        """The agent the form describes, taken in on the computer: the form
        goes and the computer's box is asked for again; or the form stays,
        as it was filled in, with why not."""

        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
        form = await request.form()
        agent, secrets, env = configuration(
            {key: str(value) for key, value in form.items()}
        )
        reply = str(form.get("reply", "")).strip()
        failed = await create(self._controls, selected, agent, secrets, env, reply)
        if failed is None:
            response = self._render.fragment(request, "agent_new.html", created=True)
            response.headers["HX-Trigger"] = "agents-changed"
            return response
        # the computer turning the configuration down says what is wrong with
        # it, not that asking failed
        word, _, code = failed.partition(":")
        if word == "refused" and code in {"REFUSED", "INVALID_REQUEST"}:
            failed = f"invalid:{code}"
        return self._render.fragment(
            request,
            "agent_new.html",
            selected=selected,
            values=agent,
            reply=reply,
            failed=failed,
            secrets=SECRETS,
        )

    @allowed("computers.view")
    @expanded("computer_id")
    @sees("computer", "computer_id")
    async def kinds(self, request: Request) -> Response:
        """What a card of one family can be started as on the computer: the
        blank page of a form's stack, filled in when it is first seen."""

        family = request.path_params["family"]
        if family not in {"channel", "runtime"}:
            return HTMLResponse("", status_code=404)
        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
        return self._render.fragment(
            request,
            "agent_kinds.html",
            family=family,
            listed=await kinds(
                self._controls, selected.computer.id, family, online=selected.online
            ),
            secrets=SECRETS,
        )

    @allowed("computers.view")
    @expanded("computer_id")
    @sees("computer", "computer_id")
    async def models(self, request: Request) -> Response:
        """The models one runtime on the computer will answer as, as the
        options of a form's model field when it is first opened; or why
        not, which the field shows on the way to asking again."""

        selected = await self._selected(request)
        if selected is None:
            return HTMLResponse("", status_code=404)
        listed = await models(
            self._controls,
            selected.computer.id,
            request.path_params["kind"],
            online=selected.online,
        )
        if listed.answer != "listed":
            return HTMLResponse(
                self._render.translator(request).text(
                    "agents." + listed.answer, {"code": listed.code}
                ),
                status_code=502,
            )
        return self._render.fragment(request, "agent_models.html", models=listed.models)

    async def _selected(self, request: Request) -> ComputerView | None:
        selected = await computer_view(
            self._storage, Access.of(request), request.path_params["computer_id"]
        )
        if selected is not None:
            await self.refs.load(_named((), selected))
        return selected

    @allowed("computers.create")
    async def enrol_form(self, request: Request) -> Response:
        return self._render.fragment(request, "enrol_form.html")

    @allowed("computers.create")
    async def enrol(self, request: Request) -> Response:
        form = await request.form()
        # a name is cut to what a row shows, like the names nodes report
        name = str(form.get("name", "")).strip()[:MAX_NAME_CHARS]
        if not name:
            # the box stays up, the page behind it as it was
            response = await self.enrol_form(request)
            response.headers["HX-Retarget"] = "#enrol"
            response.headers["HX-Reswap"] = "outerHTML"
            return response
        enrolment = await self._storage.add_computer(
            name, owner_id=Access.of(request).account.id
        )
        page, item = await asyncio.gather(
            fleet(self._storage, Access.of(request)),
            computer_view(self._storage, Access.of(request), enrolment.computer.id),
        )
        await self.refs.load(_named(page.computers, item))
        # the module comes back open on the new computer: its row picked, its
        # page under the commands that connect it, shown this once
        key = str(self.refs.ref(enrolment.computer.id))
        response = self._render.page(
            request,
            "computers",
            "computers.html",
            fleet=page,
            selected=item,
            selected_key=key,
            enrolment=enrolment,
            item=item,
            base_url=str(request.base_url).rstrip("/"),
            system=_system_of(request),
        )
        response.headers["HX-Push-Url"] = f"/computers/{key}"
        return response


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
