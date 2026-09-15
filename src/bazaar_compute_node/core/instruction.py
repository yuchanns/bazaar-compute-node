"""Provider-neutral developer instructions for runtime sessions."""

from __future__ import annotations

from dataclasses import dataclass

from ..rendering import TextTemplate
from .actor import Mode

_DEVELOPER_INSTRUCTIONS = TextTemplate.from_resource("developer_instructions.md")


@dataclass(frozen=True, slots=True)
class DeveloperInstructionContext:
    agent_name: str
    # the names the agent goes by on its channels, one per bot that has one
    bot_names: tuple[str, ...]
    agent_id: str
    runtime_session_id: str
    runtime: str
    workspace: str
    mode: Mode = Mode.SESSION

    def __post_init__(self) -> None:
        for field_name, value in (
            ("agent_name", self.agent_name),
            ("agent_id", self.agent_id),
            ("runtime_session_id", self.runtime_session_id),
            ("runtime", self.runtime),
            ("workspace", self.workspace),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
            if "\r" in value or "\n" in value:
                raise ValueError(f"{field_name} must not contain line breaks")
        for bot_name in self.bot_names:
            if not isinstance(bot_name, str) or not bot_name:
                raise ValueError("bot_names must be non-empty text")
            if "\r" in bot_name or "\n" in bot_name:
                raise ValueError("bot_names must not contain line breaks")

    def render(self) -> str:
        return _DEVELOPER_INSTRUCTIONS.render(
            {
                "agent_name": self.agent_name,
                "bot_names": self.bot_names,
                "agent_id": self.agent_id,
                "runtime_session_id": self.runtime_session_id,
                "runtime": self.runtime,
                "workspace": self.workspace,
                "mode": self.mode.value,
            }
        )


__all__ = ["DeveloperInstructionContext"]
