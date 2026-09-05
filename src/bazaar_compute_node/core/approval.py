from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ..i18n import Translator
from .actor import Actor
from .models import ApprovalRequest, ApprovalResult

_ACTION_MESSAGE_KEYS = {
    "command_execution": "approval.action.command_execution",
    "file_change": "approval.action.file_change",
    "permissions": "approval.action.permissions",
}
_MAX_APPROVAL_DESCRIPTION = 4_000
APPROVAL_DETAIL_KEYS = (
    "reason",
    "command",
    "grant_root",
    "cwd",
    "tool_name",
    "tool_input",
    "permissions",
)


def approval_action_text(translator: Translator, action: str) -> str:
    message_key = _ACTION_MESSAGE_KEYS.get(action)
    return (
        translator.text(message_key)
        if message_key is not None
        else action.replace("_", " ")
    )


def approval_description_text(
    translator: Translator,
    details: Mapping[str, str],
) -> str:
    rendered = translator.text(
        "approval.description",
        {key: details.get(key, "") for key in APPROVAL_DETAIL_KEYS},
    ).strip()
    if len(rendered) > _MAX_APPROVAL_DESCRIPTION:
        return rendered[: _MAX_APPROVAL_DESCRIPTION - 1] + "…"
    return rendered


def resolved_callback_text(translator: Translator, state: str | None) -> str:
    if state == "approved":
        return translator.text("approval.callback.already_approved")
    if state == "rejected":
        return translator.text("approval.callback.already_rejected")
    return translator.text("approval.callback.invalid")


class IApprovalHandler(Protocol):
    """Neutral callback used by a runtime adapter for approval requests."""

    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        """Route one request to the current Channel approval policy."""
        ...


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """Route one runtime approval request to its current Channel session."""

    request_id: str
    actor: Actor
    channel_session_id: str
    runtime_session_id: str
    turn_id: str | None = None

    def matches(self, request: ApprovalRequest) -> bool:
        """Ensure a response is returned to the same runtime request context."""

        return (
            self.request_id == request.request_id
            and self.actor == request.actor
            and self.runtime_session_id == request.runtime_session_id
            and self.turn_id == request.turn_id
        )
