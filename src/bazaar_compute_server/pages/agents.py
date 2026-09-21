"""Agents: who is running where, and what each is doing."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed, sees
from ..activity import recent_lines, usage_today
from ..contacts import contacts
from ..control import Controls
from ..fleet import PAGE_SIZE, AgentPage, AgentView, agent_page, agent_view
from ..history import Contact, earlier, later, latest, news
from ..refs import Refs, expanded
from ..rendering import Renderer
from ..review import Request as ReviewRequest
from ..review import decide, reminders, set_reply
from ..review import reply as reply_of
from ..review import request as request_of
from ..storage import IStorage


class AgentPages:
    def __init__(
        self, storage: IStorage, controls: Controls, renderer: Renderer, refs: Refs
    ) -> None:
        self._storage = storage
        self._controls = controls
        self._render = renderer
        self.refs = refs

    @allowed("agents.view")
    async def list(self, request: Request) -> Response:
        """The module with nothing open."""

        return await self._page(
            request, await agent_page(self._storage, Access.of(request)), None
        )

    @allowed("agents.view")
    @expanded("computer_id", "agent_id")
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
        return await self._page(request, page, selected)

    @allowed("agents.view")
    @expanded("computer_id", "agent_id", "thread_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def show_contact(self, request: Request) -> Response:
        """The module, open on one of an agent's conversations; the row it
        was opened from says what the conversation is called."""

        params = request.path_params
        contact = await self._contact(request)
        if contact is None:
            return HTMLResponse("", status_code=404)
        # htmx names the element it will swap as `tag#id`
        if request.headers.get("HX-Target") == "div#chat":
            # picked from the list: the column alone, the lists staying put
            selected = await agent_view(
                self._storage,
                Access.of(request),
                params["computer_id"],
                params["agent_id"],
            )
            if selected is None:
                return HTMLResponse("", status_code=404)
            await self.refs.load([*_named([selected]), *contact.named])
            return await self._chat(request, selected, contact)
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
        return await self._page(request, page, selected, contact)

    async def _page(
        self,
        request: Request,
        page: AgentPage,
        selected: AgentView | None,
        contact: Contact | None = None,
    ) -> Response:
        await self.refs.load(
            [
                *_named([*page.agents, *([selected] if selected else [])]),
                *(contact.named if contact else []),
            ]
        )
        # a conversation still waiting opens on the question about it
        asked = (
            await request_of(self._controls, selected, contact)
            if selected is not None and contact is not None and _pending(request)
            else None
        )
        return self._render.page(
            request,
            "agents",
            "agents.html",
            page=page,
            selected=selected,
            selected_key=(
                None
                if selected is None
                else f"{self.refs.ref(selected.computer_id)}/{self.refs.ref(selected.id)}"
            ),
            contact=contact,
            latest=request.query_params.get("latest"),
            asked=asked,
        )

    async def _chat(
        self, request: Request, selected: AgentView, contact: Contact
    ) -> Response:
        """The right column for one conversation: the chat, or, for one still
        waiting to be looked at, the question about it."""

        if _pending(request):
            return self._render.fragment(
                request,
                "chat.html",
                selected=selected,
                contact=contact,
                latest=None,
                asked=await request_of(self._controls, selected, contact),
            )
        return self._render.fragment(
            request,
            "chat.html",
            selected=selected,
            contact=contact,
            latest=request.query_params.get("latest"),
            asked=None,
        )

    @allowed("agents.approve")
    @expanded("computer_id", "agent_id", "thread_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def review(self, request: Request) -> Response:
        """Let a conversation in or turn it away; the column comes back as
        the chat when let in, empty when turned away - and the page's address
        follows: the conversation's own once let in, the agent's once turned
        away, so a reload does not ask the question again or open what was
        turned away."""

        params = request.path_params
        contact = await self._contact(request)
        if contact is None:
            return HTMLResponse("", status_code=404)
        form = await request.form()
        review = str(form.get("review", ""))
        if review not in ("approved", "denied"):
            return HTMLResponse("", status_code=400)
        selected = await agent_view(
            self._storage, Access.of(request), params["computer_id"], params["agent_id"]
        )
        if selected is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load([*_named([selected]), *contact.named])
        failed = await decide(self._controls, selected, contact.thread_id, review)
        if failed is not None and not _pending(request):
            # asked from the card about a conversation already let in: the
            # question stays up with the answer, the chat behind it as it was
            response = self._render.fragment(
                request,
                "remove_ask.html",
                selected=selected,
                contact=contact,
                failed=failed,
            )
            response.headers["HX-Retarget"] = "#remove-ask"
            return response
        if failed is not None:
            word, _, code = failed.partition(":")
            return self._render.fragment(
                request,
                "chat.html",
                selected=selected,
                contact=contact,
                latest=None,
                asked=ReviewRequest(
                    selected, contact, None, None, None, word, code or None
                ),
            )
        agent_url = f"/agents/{self.refs.ref(selected.computer_id)}/{self.refs.ref(selected.id)}"
        if review == "denied":
            response = self._render.fragment(
                request,
                "chat.html",
                selected=selected,
                contact=None,
                latest=None,
                asked=None,
            )
            response.headers["HX-Push-Url"] = agent_url
            return response
        latest = request.query_params.get("latest")
        response = self._render.fragment(
            request,
            "chat.html",
            selected=selected,
            contact=contact,
            latest=latest,
            asked=None,
        )
        response.headers["HX-Push-Url"] = (
            f"{agent_url}/contacts/{self.refs.ref(contact.thread_id)}?"
            + urlencode(
                {
                    "actor": self.refs.ref(contact.actor_id),
                    "target": self.refs.ref(contact.target),
                    "channel": contact.channel,
                    "name": contact.name,
                    **({"latest": latest} if latest else {}),
                }
            )
        )
        return response

    @allowed("agents.view")
    @expanded("computer_id", "agent_id", "thread_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def profile(self, request: Request) -> Response:
        """The card about a conversation: who is in it, what wakes the agent
        in it, and the way to turn it away."""

        params = request.path_params
        contact = await self._contact(request)
        if contact is None:
            return HTMLResponse("", status_code=404)
        selected = await agent_view(
            self._storage, Access.of(request), params["computer_id"], params["agent_id"]
        )
        if selected is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load([*_named([selected]), *contact.named])
        return self._render.fragment(
            request,
            "profile.html",
            selected=selected,
            contact=contact,
            reminders=await reminders(self._controls, selected, contact),
            failed=None,
        )

    @allowed("agents.approve")
    @expanded("computer_id", "agent_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def reply(self, request: Request) -> Response:
        """What the agent says to a conversation still waiting: the card to
        change it, or the change made."""

        params = request.path_params
        selected = await agent_view(
            self._storage, Access.of(request), params["computer_id"], params["agent_id"]
        )
        if selected is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load(_named([selected]))
        if request.method == "POST":
            form = await request.form()
            failed = await set_reply(
                self._controls, selected, str(form.get("reply", ""))
            )
            return self._render.fragment(
                request,
                "reply_form.html",
                selected=selected,
                reply=str(form.get("reply", "")),
                saved=failed is None,
                failed=failed,
            )
        # a line that could not be read is not offered for saving, or a
        # blank would go out in its place
        answer = await reply_of(self._controls, selected)
        failed = answer.partition(":")[0] in ("offline", "silent", "refused")
        return self._render.fragment(
            request,
            "reply_form.html",
            selected=selected,
            reply=None if failed else answer,
            saved=False,
            failed=answer if failed else None,
        )

    async def _ids(self, key: str | None) -> str | None:
        """The ids behind a row key as a link names it; nothing for no key,
        or one that names nothing."""

        if not key:
            return None
        ids = await self.refs.values(key.split("/"))
        return None if ids is None or len(ids) != 2 else "/".join(ids)

    async def _contact(self, request: Request) -> Contact | None:
        """The conversation a link names: its thread from the path, who it is
        answered as and its target by their numbers; nothing for numbers
        that name nothing."""

        query = request.query_params
        named = await self.refs.values(
            [query.get("actor", ""), query.get("target", "")]
        )
        if named is None:
            return None
        actor_id, target = named
        return Contact(
            thread_id=request.path_params["thread_id"],
            actor_id=actor_id,
            target=target,
            channel=query["channel"],
            name=query["name"],
        )

    @allowed("agents.view")
    async def list_fragment(self, request: Request) -> Response:
        """The list alone, for its own refresh, as far as `until`; or the rows
        of the page past `after`, for the scroll. `selected` names the open row."""

        query = request.query_params
        after, until = await asyncio.gather(
            self._ids(query.get("after")), self._ids(query.get("until"))
        )
        page = await agent_page(
            self._storage, Access.of(request), after=after, until=until
        )
        await self.refs.load(_named(page.agents))
        return self._render.fragment(
            request,
            "agent_rows.html" if after else "agent_list.html",
            page=page,
            selected_key=query.get("selected") or None,
        )

    @allowed("agents.view")
    @expanded("computer_id", "agent_id")
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
        await self.refs.load(_named([agent]))
        return self._render.fragment(
            request, "activity_card.html", agent=agent, activity=activity, usage=usage
        )

    @allowed("agents.view")
    @expanded("computer_id", "agent_id")
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
        # a computer going quiet or coming back leaves no message event; the
        # column says what it showed last, and is redrawn when that changed
        offline = agent.status == "offline"
        if since is not None and offline == (query.get("shown") == "offline"):
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
            # the column shows those let in, or, on its other tab, those waiting
            review="pending" if query.get("review") == "pending" else "approved",
        )
        # a refresh that got no answer leaves the column as it was; it is
        # asked for again when the next message event comes
        if since is not None and listing.answer in ("silent", "refused"):
            return Response(status_code=204)
        await self.refs.load(
            [
                *_named([agent]),
                *(value for row in listing.contacts for value in row.named),
            ]
        )
        return self._render.fragment(
            request,
            "contact_rows.html" if offset else "contacts.html",
            listing=listing,
            selected_thread=query.get("selected") or None,
        )

    @allowed("agents.view")
    @expanded("computer_id", "agent_id", "thread_id")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def messages(self, request: Request) -> Response:
        """A conversation's messages: the page ending at `latest` when the
        column opens, the page before `before` as the top scrolls in, or what
        came after `last` once a message event newer than `since` says
        something did - and nothing at all until one does."""

        params = request.path_params
        query = request.query_params
        agent = await agent_view(
            self._storage, Access.of(request), params["computer_id"], params["agent_id"]
        )
        if agent is None:
            return HTMLResponse("", status_code=404)
        contact = await self._contact(request)
        if contact is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load([*_named([agent]), *contact.named])
        since = query.get("since")
        last = query.get("last")
        if since is not None and last is not None:
            history = await later(
                self._storage,
                self._controls,
                agent,
                contact,
                after=last,
                since=int(since),
                shown=query.get("shown"),
            )
            if history is None:
                return Response(status_code=204)
            return self._render.fragment(request, "history_tail.html", history=history)
        # a column with no message to read after - empty, or one that got
        # none - is read afresh once something is new, and left until then
        if since is not None and (
            await news(
                self._storage,
                agent,
                contact,
                since=int(since),
                shown=query.get("shown"),
            )
            is None
        ):
            return Response(status_code=204)
        before = query.get("before")
        if before is not None:
            history = await earlier(
                self._storage, self._controls, agent, contact, before=before
            )
            return self._render.fragment(
                request, "history_rows.html", history=history, earlier=True
            )
        history = await latest(
            self._storage, self._controls, agent, contact, around=query.get("latest")
        )
        return self._render.fragment(
            request, "history.html", history=history, latest=query.get("latest")
        )


def _pending(request: Request) -> bool:
    """Whether the link came from the list of those waiting."""

    return request.query_params.get("review") == "pending"


def _named(agents: Iterable[AgentView]) -> list[str]:
    """What a page names agents by in a link: their computer, and them."""

    return [value for agent in agents for value in (agent.computer_id, agent.id)]


__all__ = ["AgentPages"]
