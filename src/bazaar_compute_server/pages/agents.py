"""Agents: who is running where, and what each is doing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from string import ascii_lowercase, ascii_uppercase
from urllib.parse import urlencode

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from ..access import Access, allowed, sees
from ..activity import recent_lines, usage_today
from ..clock import day_text, now_ms
from ..contacts import contact_name, contacts
from ..control import Controls
from ..fleet import PAGE_SIZE, AgentPage, AgentView, agent_page, agent_view
from ..history import Contact, earlier, later, latest, news
from ..permissions import Permission
from ..poll_rendering import activity_state, agent_info
from ..polling import CARD_QUIET, REMINDERS
from ..refs import Refs, expanded
from ..rendering import Renderer, identicon
from ..review import Request as ReviewRequest
from ..review import decide, reminders
from ..review import request as request_of
from ..search import SearchOptionsQuery, SearchQuery
from ..storage import IStorage


class AgentPages:
    def __init__(
        self, storage: IStorage, controls: Controls, renderer: Renderer, refs: Refs
    ) -> None:
        self._storage = storage
        self._controls = controls
        self._render = renderer
        self.refs = refs

    @allowed(Permission.AGENTS_VIEW)
    async def list(self, request: Request) -> Response:
        """The module with nothing open."""

        return await self._page(
            request, await agent_page(self._storage, Access.of(request)), None
        )

    @expanded("computer_id", "agent_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
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

    @expanded("computer_id", "agent_id", "thread_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
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
        if (
            request.headers.get("HX-Target") == "div#chat"
            and request.headers.get("HX-History-Restore-Request") != "true"
        ):
            # picked from the list: the column alone, the lists staying put
            selected = await agent_view(
                self._storage,
                Access.of(request),
                params["computer_id"],
                params["agent_id"],
            )
            if selected is None:
                return HTMLResponse("", status_code=404)
            await self.refs.load([*named([selected]), *contact.named])
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
        *,
        pending: bool | None = None,
    ) -> Response:
        await self.refs.load(
            [
                *named([*page.agents, *([selected] if selected else [])]),
                *(contact.named if contact else []),
            ]
        )
        if pending is None:
            pending = _pending(request)
        # a conversation still waiting opens on the question about it
        asked = (
            await request_of(self._controls, selected, contact)
            if selected is not None and contact is not None and pending
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
            pending=pending,
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

    @expanded("computer_id", "agent_id", "thread_id")
    @allowed(Permission.AGENTS_APPROVE, "agent")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def review(self, request: Request) -> Response:
        """Let a conversation in or turn it away, updating the module and
        address together so its tabs, selected row and chat agree on reload."""

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
        await self.refs.load([*named([selected]), *contact.named])
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
        response = await self._page(
            request,
            await agent_page(self._storage, Access.of(request)),
            selected,
            contact if review == "approved" else None,
            pending=False,
        )
        response.headers["HX-Retarget"] = "#main"
        response.headers["HX-Reswap"] = "innerMorph"
        if review == "denied":
            response.headers["HX-Push-Url"] = agent_url
            return response
        latest = request.query_params.get("latest")
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

    @expanded("computer_id", "agent_id", "thread_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
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
        await self.refs.load([*named([selected]), *contact.named])
        since = await self._storage.latest_named_event(
            selected.computer_id,
            selected.id,
            names=REMINDERS,
            thread_id=contact.thread_id,
        )
        return self._render.fragment(
            request,
            "profile.html",
            reminder_since=since,
            selected=selected,
            contact=contact,
            reminders=await reminders(self._controls, selected, contact),
            failed=None,
        )

    @expanded("computer_id", "agent_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def row_fragment(self, request: Request) -> Response:
        agent = await agent_view(
            self._storage,
            Access.of(request),
            request.path_params["computer_id"],
            request.path_params["agent_id"],
        )
        if agent is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load(named([agent]))
        return self._render.fragment(
            request,
            "agent_row.html",
            agent=agent,
            selected_key=request.query_params.get("selected"),
            poll_state=agent_info(agent),
        )

    @expanded("computer_id", "agent_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def head(self, request: Request) -> Response:
        selected = await agent_view(
            self._storage,
            Access.of(request),
            request.path_params["computer_id"],
            request.path_params["agent_id"],
        )
        if selected is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load(named([selected]))
        return self._render.fragment(
            request,
            "agent_contacts_head.html",
            selected=selected,
            poll_state=agent_info(selected),
        )

    @expanded("computer_id", "agent_id", "thread_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def reminder_fragment(self, request: Request) -> Response:
        selected = await agent_view(
            self._storage,
            Access.of(request),
            request.path_params["computer_id"],
            request.path_params["agent_id"],
        )
        contact = await self._contact(request)
        if selected is None or contact is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load([*named([selected]), *contact.named])
        since = await self._storage.latest_named_event(
            selected.computer_id,
            selected.id,
            names=REMINDERS,
            thread_id=contact.thread_id,
        )
        listed = await reminders(self._controls, selected, contact)
        return self._render.fragment(
            request,
            "conversation_reminders.html",
            selected=selected,
            contact=contact,
            reminders=listed,
            reminder_since=since,
            poll_state={"since": since, "offline": selected.status == "offline"}
            if listed.answer in ("listed", "offline")
            else None,
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

    @allowed(Permission.AGENTS_VIEW)
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
        await self.refs.load(named(page.agents))
        return self._render.fragment(
            request,
            "agent_rows.html" if after else "agent_list.html",
            page=page,
            selected_key=query.get("selected") or None,
        )

    @expanded("computer_id", "agent_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def activity_card(self, request: Request) -> Response:
        computer_id = request.path_params["computer_id"]
        agent_id = request.path_params["agent_id"]
        tz = self._render.zone(request)
        since = await self._storage.latest_activity_event(
            computer_id, agent_id, skipping=CARD_QUIET
        )
        day = day_text(now_ms(), tz)
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
        await self.refs.load(named([agent]))
        return self._render.fragment(
            request,
            "activity_card.html",
            agent=agent,
            activity=activity,
            usage=usage,
            poll_state=activity_state(agent, since, day),
        )

    @expanded("computer_id", "agent_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
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
                return Response(
                    status_code=204,
                    headers={
                        "X-Poll-State": json.dumps(
                            {"since": int(since), "offline": offline}
                        )
                    },
                )
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
                *named([agent]),
                *(value for row in listing.contacts for value in row.named),
            ]
        )
        return self._render.fragment(
            request,
            "contact_rows.html" if offset else "contacts.html",
            listing=listing,
            selected_thread=query.get("selected") or None,
        )

    @expanded("computer_id", "agent_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def search(self, request: Request) -> Response:
        """A page of results rendered from the selected agent's own node."""

        try:
            query = SearchQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return HTMLResponse("", status_code=400)
        params = request.path_params
        agent = await agent_view(
            self._storage, Access.of(request), params["computer_id"], params["agent_id"]
        )
        if agent is None:
            return HTMLResponse("", status_code=404)
        result = await self._controls.outcome(
            agent.computer_id,
            {
                "read": "search",
                "agent_id": agent.id,
                **query.model_dump(exclude={"version"}),
            },
            online=agent.status != "offline",
        )
        await self.refs.load(named([agent]))
        scope = f"{self.refs.ref(agent.computer_id)}/{self.refs.ref(agent.id)}"
        descriptor = {"scope": scope, "version": query.version, "offset": query.offset}
        if isinstance(result, str):
            word, _, code = result.partition(":")
            return self._render.fragment(
                request,
                "search_results.html",
                rows=[],
                descriptor=descriptor,
                answer=word,
                code=code or None,
            )
        await self.refs.load(
            value
            for item in result["messages"]
            for value in (item["thread_id"], item["actor_id"], item["canonical_target"])
        )
        rows = []
        for item in result["messages"]:
            name = contact_name(item["target"])
            sender = item["sender"] or {}
            speaker = (
                agent.name
                if item["direction"] == "outbound"
                else sender.get("display_name")
                or sender.get("name")
                or sender.get("id")
                or ""
            )
            url = (
                f"/agents/{scope}/contacts/{self.refs.ref(item['thread_id'])}?"
                + urlencode(
                    {
                        "actor": self.refs.ref(item["actor_id"]),
                        "target": self.refs.ref(item["canonical_target"]),
                        "channel": item["channel"],
                        "name": name,
                        "latest": item["message_id"],
                    }
                )
            )
            if item["direction"] == "outbound":
                token = "self"
            elif sender.get("name"):
                token = "@" + sender["name"].translate(
                    str.maketrans(ascii_uppercase, ascii_lowercase)
                )
            elif sender.get("id"):
                token = "@" + sender["id"]
            else:
                token = ""
            rows.append(
                {
                    "message_id": item["message_id"],
                    "thread_id": item["thread_id"],
                    "target": item["canonical_target"],
                    "url": url,
                    "name": ("#" if item["target_kind"] == "group" else "") + name,
                    "speaker": speaker,
                    "avatar": identicon(
                        agent.name
                        if item["direction"] == "outbound"
                        else sender.get("id") or "anonymous"
                    ),
                    "sender": token,
                    "at_ms": item["at_ms"],
                    "parts": item["parts"],
                }
            )
        return self._render.fragment(
            request,
            "search_results.html",
            rows=rows,
            descriptor={
                **descriptor,
                "has_more": result["has_more"],
                "next_offset": result["next_offset"],
                "sort": result["sort"],
            },
            answer="listed",
            code=None,
        )

    @expanded("computer_id", "agent_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
    @sees("computer", "computer_id")
    @sees("agent", "agent_id")
    async def search_options(self, request: Request) -> Response:
        """Conversation options from the existing contacts read."""

        try:
            query = SearchOptionsQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({}, status_code=400)
        agent = await agent_view(
            self._storage,
            Access.of(request),
            request.path_params["computer_id"],
            request.path_params["agent_id"],
        )
        if agent is None:
            return JSONResponse({}, status_code=404)
        result = await self._controls.outcome(
            agent.computer_id,
            {
                "read": "contacts",
                "agent_id": agent.id,
                "review": None,
                "limit": query.limit,
                "offset": query.offset,
            },
            online=agent.status != "offline",
        )
        if isinstance(result, str):
            word, _, code = result.partition(":")
            return JSONResponse({"answer": word, "code": code or None})
        options = [
            {
                "token": item["canonical_target"],
                "label": ("#" if item["target_kind"] == "group" else "")
                + contact_name(item["target"]),
                "avatar": identicon(item["thread_id"]),
            }
            for item in result["targets"]
        ]
        return JSONResponse(
            {
                "answer": "listed",
                "options": options,
                "has_more": result["has_more"],
                "next_offset": query.offset + len(options),
            },
            headers={"Cache-Control": "private, no-store"},
        )

    @expanded("computer_id", "agent_id", "thread_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
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
        await self.refs.load([*named([agent]), *contact.named])
        since = query.get("since")
        last = query.get("last")
        if since is not None and last is not None:
            history = await later(
                self._storage,
                self._controls,
                agent,
                contact,
                self._render.zone(request),
                after=last,
                since=int(since),
                shown=query.get("shown"),
            )
            if history is None:
                return Response(
                    status_code=204,
                    headers={
                        "X-Poll-State": json.dumps(
                            {"since": int(since), "offline": agent.status == "offline"}
                        )
                    },
                )
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
            return Response(
                status_code=204,
                headers={
                    "X-Poll-State": json.dumps(
                        {"since": int(since), "offline": agent.status == "offline"}
                    )
                },
            )
        before = query.get("before")
        if before is not None:
            history = await earlier(
                self._storage,
                self._controls,
                agent,
                contact,
                self._render.zone(request),
                before=before,
            )
            return self._render.fragment(
                request, "history_rows.html", history=history, earlier=True
            )
        history = await latest(
            self._storage,
            self._controls,
            agent,
            contact,
            self._render.zone(request),
            around=query.get("latest"),
        )
        return self._render.fragment(
            request, "history.html", history=history, latest=query.get("latest")
        )


def _pending(request: Request) -> bool:
    """Whether the link came from the list of those waiting."""

    return request.query_params.get("review") == "pending"


def named(agents: Iterable[AgentView]) -> list[str]:
    """What a page names agents by in a link: their computer, and them."""

    return [value for agent in agents for value in (agent.computer_id, agent.id)]


__all__ = ["AgentPages", "named"]
