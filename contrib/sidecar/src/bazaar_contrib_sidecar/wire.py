"""JSON values, installed symbols, and connection-owned object references."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal
lazy from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field, ImportString, TypeAdapter


@dataclass(frozen=True, slots=True)
class Symbol:
    """An installed public symbol resolved in the server process."""

    path: str


@dataclass(frozen=True, slots=True)
class Construct:
    """Construct an installed type in the server process with encoded parameters."""

    path: str
    fields: Mapping[str, object]


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Atom(Message):
    kind: Literal["value"] = "value"
    value: None | bool | int | float | str


class Reference(Message):
    kind: Literal["reference"] = "reference"
    id: int = Field(ge=0)


class Imported(Message):
    kind: Literal["import"] = "import"
    path: str = Field(min_length=1)


class Built(Message):
    kind: Literal["construct"] = "construct"
    path: str = Field(min_length=1)
    fields: dict[str, Value]


class Sequence(Message):
    kind: Literal["list", "tuple"]
    items: tuple[Value, ...]


class Fields(Message):
    kind: Literal["dict"] = "dict"
    items: dict[str, Value]


type Value = Annotated[
    Atom | Reference | Imported | Built | Sequence | Fields,
    Field(discriminator="kind"),
]


class Request(Message):
    operation: Literal["get", "call", "enter", "exit", "release"]
    target: int = Field(default=0, ge=0)
    method: str = Field(default="", pattern=r"^(?:[A-Za-z][A-Za-z0-9_]*|)$")
    args: tuple[Value, ...] = ()
    kwargs: dict[str, Value] = Field(default_factory=dict)


class Result(Message):
    kind: Literal["result"] = "result"
    value: Value


class Failure(Message):
    kind: Literal["error"] = "error"
    type: str
    message: str


for model in (Built, Sequence, Fields, Request, Result):
    model.model_rebuild()

RESPONSE = TypeAdapter(Annotated[Result | Failure, Field(discriminator="kind")])
IMPORT = TypeAdapter(ImportString[Any])


def encode(value: object, reference: Callable[[object], Reference]) -> Value:
    match value:
        case Symbol(path):
            return Imported(path=path)
        case Construct(path, fields):
            return Built(
                path=path,
                fields={key: encode(item, reference) for key, item in fields.items()},
            )
        case None | bool() | int() | float() | str():
            return Atom(value=value)
        case list() | tuple():
            return Sequence(
                kind="tuple" if isinstance(value, tuple) else "list",
                items=tuple(encode(item, reference) for item in value),
            )
        case dict() if all(isinstance(key, str) for key in value):
            return Fields(
                items={key: encode(item, reference) for key, item in value.items()}
            )
        case _:
            return reference(value)


def decode(value: Value, reference: Callable[[Reference], object]) -> Any:
    match value:
        case Atom():
            return value.value
        case Reference():
            return reference(value)
        case Imported():
            return IMPORT.validate_python(value.path)
        case Built():
            return IMPORT.validate_python(value.path)(
                **{key: decode(item, reference) for key, item in value.fields.items()}
            )
        case Sequence():
            items = [decode(item, reference) for item in value.items]
            return tuple(items) if value.kind == "tuple" else items
        case Fields():
            return {key: decode(item, reference) for key, item in value.items.items()}
