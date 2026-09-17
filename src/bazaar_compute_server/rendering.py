"""Turning a page's values into HTML: the templates, their filters, and the
choice between a whole page and the fragment htmx asked for."""

from __future__ import annotations

from datetime import tzinfo
from functools import lru_cache
from hashlib import blake2b
from typing import Any

from jinja2 import (
    Environment,
    PackageLoader,
    StrictUndefined,
    pass_context,
    select_autoescape,
)
from jinja2.runtime import Context
from starlette.requests import Request
from starlette.responses import HTMLResponse

from .clock import clock_text, now_ms, zone
from .i18n import Translator, create_translator, language_from_header


class Renderer:
    def __init__(self, lang: str | None) -> None:
        self._lang = lang
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

    def translator(self, request: Request) -> Translator:
        return create_translator(
            self._lang or language_from_header(request.headers.get("Accept-Language"))
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
            return HTMLResponse(self._templates.get_template(template).render(**values))
        return HTMLResponse(
            self._templates.get_template("shell.html").render(
                content=template, **values
            )
        )

    def fragment(self, request: Request, template: str, **values: Any) -> HTMLResponse:
        return HTMLResponse(
            self._templates.get_template(template).render(
                **self._viewer(request), **values
            )
        )

    def _viewer(self, request: Request) -> dict[str, Any]:
        """What every template knows about who is looking: their words, their clock."""

        return {"t": self.translator(request), "tz": self.zone(request)}


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


__all__ = ["Renderer", "identicon"]
