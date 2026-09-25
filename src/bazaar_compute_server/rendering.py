"""Turning a page's values into HTML: the templates, their filters, and the
choice between a whole page and the fragment htmx asked for."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, tzinfo
from functools import lru_cache
from hashlib import blake2b
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

from jinja2 import (
    Environment,
    PackageLoader,
    StrictUndefined,
    pass_context,
    select_autoescape,
)
from jinja2.runtime import Context
from markupsafe import Markup
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .clock import clock_text, day_text, now_ms, time_text, zone
from .i18n import LANGUAGES, Translator, create_translator, language_from_header
from .images import Images
from .markdown import render
from .refs import Refs
from .storage import Account

# the looks an account may choose; anything else follows the system
THEMES = ("light", "dark")


class Renderer:
    def __init__(self, images: Images, refs: Refs) -> None:
        # templates and static files ship inside the package, so they are
        # read through the package, never from a directory that may not exist
        self._templates = Environment(
            loader=PackageLoader("bazaar_compute_server", "resources/templates"),
            undefined=StrictUndefined,
            autoescape=select_autoescape(("html",)),
        )
        self._templates.filters["identicon"] = identicon
        self._templates.filters["ago"] = _ago
        self._templates.filters["clock"] = _clock
        self._templates.filters["day"] = _day
        self._templates.filters["time"] = _time
        self._templates.filters["due"] = _due
        self._templates.filters["repeat"] = _repeat
        self._templates.filters["markdown"] = _markdown
        self._templates.globals["languages"] = LANGUAGES
        self._templates.globals["themes"] = THEMES
        self._templates.globals["asset"] = _asset
        self._templates.globals["build"] = BUILD
        self._templates.globals["image_address"] = images.address
        # the number a page names a loaded value by, in a link
        self._templates.globals["ref"] = refs.ref

    @staticmethod
    def translator(request: Request) -> Translator:
        """The words of whoever is looking: their chosen language once they
        are logged in and chose one, the browser's otherwise."""

        account: Account | None = getattr(request.state, "account", None)
        chosen = None if account is None else account.language
        return create_translator(
            chosen or language_from_header(request.headers.get("Accept-Language"))
        )

    @staticmethod
    def zone(request: Request) -> tzinfo:
        """The viewer's zone, which the page sends along with every request."""

        return zone(request.headers.get("X-Timezone"))

    def page(
        self, request: Request, module: str, template: str, **values: Any
    ) -> HTMLResponse:
        """A module's page: inside the shell, unless htmx is swapping it in."""

        values = {**self._viewer(request), "module": module, **values}
        if request.headers.get("HX-Request") == "true":
            # the rail stays outside the swapped region, so it rides along
            # out of band to move its highlight
            return _personal(
                self._templates.get_template(template).render(**values)
                + self._templates.get_template("rail.html").render(oob=True, **values)
            )
        return _personal(
            self._templates.get_template("shell.html").render(
                content=template, **values
            )
        )

    def fragment(
        self, request: Request, template: str, *, status_code: int = 200, **values: Any
    ) -> HTMLResponse:
        return _personal(
            self._templates.get_template(template).render(
                **self._viewer(request), **values
            ),
            status_code=status_code,
        )

    def error(self, request: Request, status_code: int) -> HTMLResponse:
        """The closed-stall board for a status, whole or as the fragment
        htmx will swap in."""

        response = self.page(request, "", "error.html", code=status_code)
        response.status_code = status_code
        return response

    def standalone(
        self, request: Request, template: str, **values: Any
    ) -> HTMLResponse:
        """A whole page that is not inside the shell, such as the login page."""

        return _personal(
            self._templates.get_template(template).render(
                **self._viewer(request), **values
            )
        )

    def _viewer(self, request: Request) -> dict[str, Any]:
        """What every template knows about who is looking: their words, their clock."""

        account: Account | None = getattr(request.state, "account", None)
        return {
            "t": self.translator(request),
            "tz": self.zone(request),
            "theme": None if account is None else account.theme,
        }


@pass_context
def _markdown(context: Context, text: str) -> Markup:
    """What was written, as the Markdown a chat is written in; a code block
    and a picture are drawn by the page's own templates."""

    code = context.environment.get_template("code.html")
    image = context.environment.get_template("image.html")
    return render(
        text,
        code=lambda content: code.render(**context.get_all(), code=content),
        image=lambda **fields: image.render(**context.get_all(), **fields),
    )


@lru_cache
def _digest(name: str) -> str:
    content = files("bazaar_compute_server").joinpath("resources", "static", name)
    return blake2b(content.read_bytes(), digest_size=6).hexdigest()


def _asset(name: str) -> str:
    """Where a static file is, under a name that changes with its content, so
    a browser holding the last one comes for the new one."""

    return f"/static/{name}?v={_digest(name)}"


def _build() -> str:
    """The whole package - code, templates, stylesheet, script - as one
    digest: a page holding another is out of date as a whole, whatever piece
    of it asks."""

    digest = blake2b(digest_size=6)
    for entry in _walk(files("bazaar_compute_server")):
        digest.update(entry.read_bytes())
    return digest.hexdigest()


def _walk(root: Traversable) -> Iterator[Traversable]:
    """Every file under the package, in one order; what Python leaves behind
    is not the package."""

    for entry in sorted(root.iterdir(), key=lambda entry: entry.name):
        if entry.is_dir():
            if entry.name != "__pycache__":
                yield from _walk(entry)
        else:
            yield entry


BUILD = _build()


class Stale:
    """Tells a page from another build so, instead of handing it a piece of
    this one: what a piece needs may no longer be there. The page then asks
    its reader to refresh."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            if headers.get("HX-Request") == "true" and headers.get("X-Build") not in (
                None,
                BUILD,
            ):
                await Response(status_code=204, headers={"HX-Trigger": "stale"})(
                    scope, receive, send
                )
                return
        await self._app(scope, receive, send)


def _personal(body: str, *, status_code: int = 200) -> HTMLResponse:
    """A response made for whoever asked: not for any cache to keep, and a
    different body for htmx than for a browser loading the page whole."""

    return HTMLResponse(
        body,
        status_code=status_code,
        headers={"Cache-Control": "private, no-store", "Vary": "HX-Request"},
    )


@pass_context
def _ago(context: Context, at_ms: int) -> str:
    """How long ago, in the words of the page's translator."""

    translator: Translator = context["t"]
    seconds = max(0, (now_ms() - at_ms) // 1000)
    if seconds < 10:
        return translator.text("time.just_now")
    if seconds < 60:
        return translator.text("time.seconds_ago", {"n": seconds})
    if seconds < 3600:
        return translator.text("time.minutes_ago", {"n": seconds // 60})
    if seconds < 86400:
        return translator.text("time.hours_ago", {"n": seconds // 3600})
    return translator.text("time.days_ago", {"n": seconds // 86400})


@pass_context
def _clock(context: Context, at_ms: int) -> str:
    """A time of day on the viewer's clock."""

    return clock_text(at_ms, context["tz"])


@pass_context
def _day(context: Context, at_ms: int) -> str:
    return day_text(at_ms, context["tz"])


@pass_context
def _time(context: Context, at_ms: int) -> str:
    return time_text(at_ms, context["tz"])


@pass_context
def _due(context: Context, at_ms: int) -> str:
    """When something is set for, the way one says it: today or tomorrow by
    name, any other day by date, and the time to the minute."""

    translator: Translator = context["t"]
    tz = context["tz"]
    moment = datetime.fromtimestamp(at_ms / 1000, tz)
    clock = moment.strftime("%H:%M")
    match (moment.date() - datetime.now(tz).date()).days:
        case 0:
            return translator.text("time.today", {"clock": clock})
        case 1:
            return translator.text("time.tomorrow", {"clock": clock})
        case _:
            return f"{moment:%Y-%m-%d} {clock}"


# the node's repeat rules, in their canonical forms
_EVERY = re.compile(r"every:(?P<n>[0-9]+)(?P<unit>[mhd])")
_WEEKLY = re.compile(r"weekly:(?P<days>[a-z,]+)@")


@pass_context
def _repeat(context: Context, rule: str) -> str:
    """A repeat rule in words: every so often, daily, or on named weekdays."""

    translator: Translator = context["t"]
    if rule.startswith("daily@"):
        return translator.text("repeat.daily")
    if every := _EVERY.fullmatch(rule):
        return translator.text(f"repeat.every_{every['unit']}", {"n": every["n"]})
    if weekly := _WEEKLY.match(rule):
        days = translator.text("repeat.day_join").join(
            translator.text(f"repeat.{day}") for day in weekly["days"].split(",")
        )
        return translator.text("repeat.weekly", {"days": days})
    return rule


# a mark is under half a KiB, so this many is a couple of MiB: every agent
# and every sender a page can name, kept for as long as they keep appearing
@lru_cache(maxsize=4096)
def identicon(name: str) -> str:
    """A 5x5 symmetric pixel mark from the name, the way GitHub draws one."""

    digest = blake2b(name.encode("utf-8"), digest_size=8).digest()
    hue = int.from_bytes(digest[:2], "big") % 360
    bits = int.from_bytes(digest[2:], "big")
    cells = []
    for y in range(5):
        for x in range(3):
            if bits & 1:
                cells.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
                if x != 2:
                    cells.append(f'<rect x="{4 - x}" y="{y}" width="1" height="1"/>')
            bits >>= 1
    return (
        f'<svg viewBox="0 0 5 5" fill="hsl({hue} 55% 48%)" shape-rendering="crispEdges">'
        + "".join(cells)
        + "</svg>"
    )


__all__ = ["BUILD", "Renderer", "Stale", "identicon"]
