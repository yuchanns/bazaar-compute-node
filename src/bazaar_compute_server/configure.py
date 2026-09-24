"""Agents taken in on a computer from the server: what the computer can run,
and a filled-in form turned into the configuration the computer keeps."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .control import Controls
from .fleet import ComputerView
from .review import REVIEW_REPLY

# the credential each kind of channel takes, by the name its option carries
# without `_env`; the value crosses beside the configuration, never in it
SECRETS = {"telegram": "token", "lark": "app_secret", "wecom": "secret"}

_FIELD = re.compile(r"(channel|runtime)-(\d+)-(.+)")
_ENV = re.compile(r"env-(\d+)-(name|value)")


@dataclass(frozen=True, slots=True)
class Kind:
    """A kind of channel or runtime installed on the computer, and whether
    it runs."""

    kind: str
    available: bool = True
    version: str | None = None


@dataclass(frozen=True, slots=True)
class Kinds:
    """What a card of one family can be started as on the computer; or why
    that is not known."""

    kinds: tuple[Kind, ...]
    answer: str
    code: str | None = None


async def kinds(
    controls: Controls, computer_id: str, family: str, *, online: bool
) -> Kinds:
    """The kinds of channel the computer has, or of runtime - each asked
    alone, when a form wants to start a card of that family."""

    answer = await controls.outcome(
        computer_id,
        {"read": "agents" if family == "channel" else "runtimes"},
        online=online,
    )
    if isinstance(answer, str):
        word, _, code = answer.partition(":")
        return Kinds((), word, code or None)
    if family == "channel":
        return Kinds(
            tuple(Kind(kind) for kind in answer["kinds"]["channels"]), "listed"
        )
    return Kinds(
        tuple(
            Kind(item["kind"], item["available"], item["version"])
            for item in answer["runtimes"]
        ),
        "listed",
    )


@dataclass(frozen=True, slots=True)
class Model:
    id: str
    name: str
    efforts: tuple[str, ...]
    # the one the runtime answers as when an agent names none
    default: bool


@dataclass(frozen=True, slots=True)
class Models:
    """The models one runtime on the computer will answer as; or why that
    is not known - `unlisted` when the runtime itself would not say."""

    models: tuple[Model, ...]
    answer: str
    code: str | None = None


async def models(
    controls: Controls, computer_id: str, kind: str, *, online: bool
) -> Models:
    answer = await controls.outcome(
        computer_id, {"read": "models", "kind": kind}, online=online
    )
    if isinstance(answer, str):
        word, _, code = answer.partition(":")
        return Models((), word, code or None)
    if answer["error"] is not None:
        return Models((), "unlisted", answer["error"])
    return Models(
        tuple(
            Model(item["id"], item["name"], tuple(item["efforts"]), item["default"])
            for item in answer["models"]
        ),
        "listed",
    )


def configuration(
    form: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """The agent the form describes, in the shape the configuration file
    holds it in; the credentials typed into it by channel position; and the
    environment values typed into it by runtime position, which the node
    keeps the way it keeps a credential.
    Cards are numbered as they were added, gaps and all; they keep their
    order. A credential left empty is not sent: nothing changes."""

    cards: dict[str, dict[int, dict[str, str]]] = {"channel": {}, "runtime": {}}
    for key, value in form.items():
        if match := _FIELD.fullmatch(key):
            family, number, field = match.groups()
            cards[family].setdefault(int(number), {})[field] = value
    channels: list[dict[str, Any]] = []
    secrets: dict[str, dict[str, str]] = {}
    for _, fields in sorted(cards["channel"].items()):
        kind = fields.pop("kind")
        secret = fields.pop("secret", "")
        if secret:
            secrets[str(len(channels))] = {SECRETS[kind]: secret}
        channels.append({"kind": kind, **{k: v for k, v in fields.items() if v}})
    runtimes: list[dict[str, Any]] = []
    values: dict[str, dict[str, str]] = {}
    for _, fields in sorted(cards["runtime"].items()):
        env: dict[int, dict[str, str]] = {}
        for field in list(fields):
            if match := _ENV.fullmatch(field):
                env.setdefault(int(match[1]), {})[match[2]] = fields.pop(field)
        typed = {
            row["name"]: row["value"]
            for _, row in sorted(env.items())
            if row.get("name") and row.get("value")
        }
        if typed:
            values[str(len(runtimes))] = typed
        runtime: dict[str, Any] = {
            "kind": fields["kind"],
            "sandbox_mode": fields["sandbox_mode"],
            "network_access": fields.get("network_access") == "on",
            "env": {},
        }
        for key in ("model", "effort"):
            if fields.get(key):
                runtime[key] = fields[key]
        runtimes.append(runtime)
    return (
        {
            "name": form.get("name", ""),
            "mode": form.get("mode", "session"),
            "idle_timeout": _number(form.get("idle_timeout", "0")),
            "channel": channels,
            "runtime": runtimes,
        },
        secrets,
        values,
    )


def _lacking(agent: Mapping[str, Any]) -> str | None:
    """The word for what an agent cannot run without: a channel to hear on
    and a runtime to think with, at least one of each."""

    if not agent["channel"]:
        return "need_channel"
    if not agent["runtime"]:
        return "need_runtime"
    return None


async def create(
    controls: Controls,
    computer: ComputerView,
    agent: Mapping[str, Any],
    secrets: Mapping[str, Mapping[str, str]],
    env: Mapping[str, Mapping[str, str]],
    reply: str,
) -> str | None:
    """Take the agent in on the computer, and with it what it tells those
    waiting to be looked at, when there is something to tell them; the word
    for why there is no agent when it did not take it in."""

    if lacking := _lacking(agent):
        return lacking
    written = await controls.outcome(
        computer.computer.id,
        {
            "write": "agent",
            "agent": dict(agent),
            "secrets": dict(secrets),
            "env": dict(env),
            "settings": {REVIEW_REPLY: reply} if reply else {},
        },
        online=computer.online,
    )
    return written if isinstance(written, str) else None


def _number(text: str) -> float | str:
    """A number typed into the form; anything else goes as typed, for the
    computer to say what is wrong with it."""

    try:
        return float(text or 0)
    except ValueError:
        return text


__all__ = [
    "SECRETS",
    "Kind",
    "Kinds",
    "Model",
    "Models",
    "configuration",
    "create",
    "kinds",
    "models",
]
