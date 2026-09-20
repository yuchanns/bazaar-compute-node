"""The pages, one module of the shell per file."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from ..control import Controls
from ..images import PATH, Images
from ..rendering import Renderer
from ..sessions import Sessions
from ..storage import IStorage
from .agents import AgentPages
from .computers import ComputerPages
from .login import LoginPages
from .settings import SettingsPages


async def home(request: Request) -> Response:
    del request
    return RedirectResponse("/agents")


def routes(
    storage: IStorage, sessions: Sessions, controls: Controls, images: Images
) -> list[Route]:
    renderer = Renderer(images)
    agents = AgentPages(storage, controls, renderer)
    computers = ComputerPages(storage, renderer)
    login = LoginPages(storage, renderer, sessions)
    settings = SettingsPages(storage, renderer, sessions)
    return [
        Route("/", home),
        Route("/login", login.form),
        Route("/login", login.login, methods=["POST"]),
        Route("/logout", login.logout, methods=["POST"]),
        Route("/settings", settings.page),
        Route("/settings/{section}", settings.page),
        Route("/settings/password", settings.change_password, methods=["POST"]),
        Route("/settings/preferences", settings.change_preferences, methods=["POST"]),
        Route("/agents", agents.list),
        Route("/agents/list", agents.list_fragment),
        Route("/agents/{computer_id}/{agent_id}", agents.show),
        Route("/agents/{computer_id}/{agent_id}/activity", agents.activity_card),
        Route("/agents/{computer_id}/{agent_id}/contacts", agents.contacts),
        Route(
            "/agents/{computer_id}/{agent_id}/contacts/{thread_id}", agents.show_contact
        ),
        Route(
            "/agents/{computer_id}/{agent_id}/contacts/{thread_id}/messages",
            agents.messages,
        ),
        Route("/computers", computers.list),
        Route("/computers/new", computers.enrol_form),
        Route("/computers/list", computers.list_fragment),
        Route("/computers", computers.enrol, methods=["POST"]),
        Route("/computers/{computer_id}", computers.show),
        Route("/computers/{computer_id}/detail", computers.detail),
        Route("/computers/{computer_id}/presence", computers.presence),
        Route("/computers/{computer_id}/remove", computers.remove_form),
        Route("/computers/{computer_id}", computers.remove, methods=["DELETE"]),
        Route(PATH, images.fetch),
    ]


__all__ = ["routes"]
