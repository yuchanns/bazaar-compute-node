"""Provider-neutral developer instructions for runtime sessions."""

from __future__ import annotations

from dataclasses import dataclass

from ..rendering import TextTemplate

_DEVELOPER_INSTRUCTIONS = TextTemplate.from_resource("developer_instructions.md")


@dataclass(frozen=True, slots=True)
class DeveloperInstructionContext:
    agent_name: str
    bot_name: str | None
    agent_id: str
    runtime_session_id: str
    runtime: str
    workspace: str

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
        if self.bot_name is not None:
            if not isinstance(self.bot_name, str) or not self.bot_name:
                raise ValueError("bot_name must be non-empty text when present")
            if "\r" in self.bot_name or "\n" in self.bot_name:
                raise ValueError("bot_name must not contain line breaks")

    def render(self) -> str:
        return _DEVELOPER_INSTRUCTIONS.render(
            {
                "agent_name": self.agent_name,
                "bot_name": self.bot_name,
                "agent_id": self.agent_id,
                "runtime_session_id": self.runtime_session_id,
                "runtime": self.runtime,
                "workspace": self.workspace,
            }
        )


__all__ = ["DeveloperInstructionContext"]
