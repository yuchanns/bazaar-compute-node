"""JSON calls to services exposed under a name, and their results."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Request(Message):
    service: str = Field(min_length=1)
    method: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    args: tuple[JsonValue, ...] = ()
    kwargs: dict[str, JsonValue] = Field(default_factory=dict)


class Result(Message):
    kind: Literal["result"] = "result"
    value: JsonValue


class Failure(Message):
    kind: Literal["error"] = "error"
    type: str
    message: str


RESPONSE = TypeAdapter(Annotated[Result | Failure, Field(discriminator="kind")])
