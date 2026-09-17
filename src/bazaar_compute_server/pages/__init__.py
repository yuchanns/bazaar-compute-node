"""The pages, one module of the shell per file."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from ..rendering import Renderer
from ..storage import IStorage
from .agents import AgentPages
from .computers import ComputerPages


async def home(request: Request) -> Response:
    del request
    return RedirectResponse("/agents")


def routes(storage: IStorage, lang: str | None) -> list[Route]:
    renderer = Renderer(lang)
    agents = AgentPages(storage, renderer)
    computers = ComputerPages(storage, renderer)
    return [
        Route("/", home),
        Route("/agents", agents.list),
        Route("/agents/{computer_id}/{agent_id}", agents.list),
        Route("/agents/{computer_id}/{agent_id}/activity", agents.activity_card),
        Route("/computers", computers.list),
        Route("/computers/new", computers.enrol_form),
        Route("/computers", computers.enrol, methods=["POST"]),
        Route("/computers/{computer_id}", computers.list),
    ]


__all__ = ["routes"]
