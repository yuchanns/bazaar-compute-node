"""The pages, one module of the shell per file."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from ..control import Controls
from ..images import PATH, Images
from ..refs import Refs
from ..rendering import Renderer
from ..sessions import Sessions
from ..storage import IStorage
from .accounts import AccountPages
from .agents import AgentPages
from .computers import ComputerPages
from .login import LoginPages
from .poll import PollPages
from .profile import ProfilePages
from .roles import RolePages
from .settings import SettingsPages


async def home(request: Request) -> Response:
    del request
    return RedirectResponse("/agents")


def routes(
    storage: IStorage,
    sessions: Sessions,
    controls: Controls,
    images: Images,
    refs: Refs,
) -> list[Route]:
    renderer = Renderer(images, refs)
    agents = AgentPages(storage, controls, renderer, refs)
    profile = ProfilePages(storage, controls, renderer, refs, agents)
    computers = ComputerPages(storage, controls, renderer, refs)
    login = LoginPages(storage, renderer, sessions)
    settings = SettingsPages(storage, renderer, sessions)
    roles = RolePages(storage, renderer, refs)
    accounts = AccountPages(storage, renderer, refs)
    poll = PollPages(storage, renderer, refs)
    return [
        Route("/poll", poll.check, methods=["POST"]),
        Route("/", home),
        Route("/login", login.form),
        Route("/login", login.login, methods=["POST"]),
        Route("/logout", login.logout, methods=["POST"]),
        Route("/settings", settings.page),
        Route("/settings/roles", roles.list),
        Route("/settings/roles", roles.create, methods=["POST"]),
        Route("/settings/roles/new", roles.new),
        Route("/settings/roles/{role_id}", roles.show),
        Route("/settings/roles/{role_id}", roles.save, methods=["POST"]),
        Route("/settings/roles/{role_id}", roles.remove, methods=["DELETE"]),
        Route("/settings/roles/{role_id}/default", roles.default, methods=["POST"]),
        Route("/settings/accounts", accounts.list),
        Route("/settings/accounts/{account_id}", accounts.show),
        Route("/settings/accounts/{account_id}", accounts.save, methods=["POST"]),
        Route("/settings/{section}", settings.page),
        Route("/settings/password", settings.change_password, methods=["POST"]),
        Route("/settings/preferences", settings.change_preferences, methods=["POST"]),
        Route("/agents", agents.list),
        Route("/agents/list", agents.list_fragment),
        Route("/agents/{computer_id}/{agent_id}", agents.show),
        Route("/agents/{computer_id}/{agent_id}/row", agents.row_fragment),
        Route("/agents/{computer_id}/{agent_id}/head", agents.head),
        Route(
            "/agents/{computer_id}/{agent_id}/contacts/{thread_id}/reminders",
            agents.reminder_fragment,
        ),
        Route("/agents/{computer_id}/{agent_id}/activity", agents.activity_card),
        Route("/agents/{computer_id}/{agent_id}/contacts", agents.contacts),
        Route(
            "/agents/{computer_id}/{agent_id}/contacts/{thread_id}", agents.show_contact
        ),
        Route(
            "/agents/{computer_id}/{agent_id}/contacts/{thread_id}/messages",
            agents.messages,
        ),
        Route(
            "/agents/{computer_id}/{agent_id}/contacts/{thread_id}/review",
            agents.review,
            methods=["POST"],
        ),
        Route(
            "/agents/{computer_id}/{agent_id}/contacts/{thread_id}/profile",
            agents.profile,
        ),
        Route("/agents/{computer_id}/{agent_id}/profile", profile.show),
        Route(
            "/agents/{computer_id}/{agent_id}/profile", profile.save, methods=["POST"]
        ),
        Route("/agents/{computer_id}/{agent_id}/profile/remove", profile.remove_ask),
        Route("/agents/{computer_id}/{agent_id}/profile/events", profile.events),
        Route("/agents/{computer_id}/{agent_id}/profile/head", profile.head),
        Route("/agents/{computer_id}/{agent_id}/profile/{tab}", profile.tab),
        Route("/agents/{computer_id}/{agent_id}", profile.remove, methods=["DELETE"]),
        Route("/computers", computers.list),
        Route("/computers/new", computers.enrol_form),
        Route("/computers/list", computers.list_fragment),
        Route("/computers", computers.enrol, methods=["POST"]),
        Route("/computers/{computer_id}", computers.show),
        Route("/computers/{computer_id}/row", computers.row_fragment),
        Route("/computers/{computer_id}/detail", computers.detail),
        Route("/computers/{computer_id}/presence", computers.presence),
        Route("/computers/{computer_id}/remove", computers.remove_form),
        Route("/computers/{computer_id}/agents/new", computers.new_agent),
        Route("/computers/{computer_id}/kinds/{family}", computers.kinds),
        Route("/computers/{computer_id}/models/{kind}", computers.models),
        Route(
            "/computers/{computer_id}/agents", computers.create_agent, methods=["POST"]
        ),
        Route("/computers/{computer_id}", computers.remove, methods=["DELETE"]),
        Route(PATH, images.fetch),
    ]


__all__ = ["routes"]
