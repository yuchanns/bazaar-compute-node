"""Render the default agent's instructions before a harness session starts."""

from __future__ import annotations

from functools import cache
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
lazy from jinja2 import Environment, PackageLoader, StrictUndefined, Template

type Line = Annotated[str, Field(min_length=1, pattern=r"^[^\r\n]+$")]


@cache
def template() -> Template:
    return Environment(
        loader=PackageLoader("bazaar_contrib_agent", "resources"),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        auto_reload=False,
    ).get_template("developer_instructions.md")


class DeveloperInstructionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_name: Line
    bot_names: tuple[Line, ...]
    agent_id: Line
    runtime_session_id: Line
    runtime: Line
    workspace: Line
    mode: Literal["session", "dangerous_individual"] = "session"

    def render(self) -> str:
        return template().render(self.model_dump())
