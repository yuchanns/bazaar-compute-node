"""The shared permission catalogue; each stable point is also its translation key."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .access import Access


class Permission(StrEnum):
    """Functional actions, grouped by the module prefix of their value."""

    COMPUTERS_VIEW = "computers.view"
    COMPUTERS_CREATE = "computers.create"
    COMPUTERS_DELETE = "computers.delete"
    COMPUTERS_SHARE = "computers.share"
    AGENTS_VIEW = "agents.view"
    AGENTS_CREATE = "agents.create"
    AGENTS_UPDATE = "agents.update"
    AGENTS_DELETE = "agents.delete"
    AGENTS_APPROVE = "agents.approve"
    SETTINGS_ROLES = "settings.roles"
    SETTINGS_ACCOUNTS = "settings.accounts"
    SETTINGS_OIDC = "settings.oidc"

    @property
    def module(self) -> str:
        return self.value.partition(".")[0]


@dataclass(frozen=True, slots=True)
class PermissionHandler:
    """A functional permission, optionally scoped to a named resource."""

    point: Permission
    kind: str | None = None

    async def __call__(self, access: Access, targets: Mapping[str, str | None]) -> bool:
        if not access.allows(self.point):
            return False
        if self.kind is None:
            return True
        target = targets.get(f"{self.kind}_id")
        return target is not None and await access.can(self.kind, target, self.point)


POLL_HANDLERS = MappingProxyType(
    {
        "computers": PermissionHandler(Permission.COMPUTERS_VIEW),
        "agents": PermissionHandler(Permission.AGENTS_VIEW),
        **dict.fromkeys(
            ("computer", "computer-state", "computer-info"),
            PermissionHandler(Permission.COMPUTERS_VIEW, "computer"),
        ),
        **dict.fromkeys(
            (
                "agent-status",
                "agent-info",
                "contacts",
                "messages",
                "events",
                "activity",
                "health",
                "reminders",
            ),
            PermissionHandler(Permission.AGENTS_VIEW, "agent"),
        ),
    }
)


SHARE_POINTS = MappingProxyType(
    {
        "computer": tuple(
            point
            for point in Permission
            if point.module in ("computers", "agents")
            and point != Permission.COMPUTERS_CREATE
        ),
        "agent": tuple(
            point
            for point in Permission
            if point.module == "agents" and point != Permission.AGENTS_CREATE
        ),
    }
)
