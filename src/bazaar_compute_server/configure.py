"""Agents taken in on a computer from the server: what the computer can run,
and a filled-in form turned into the configuration the computer keeps."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .control import Controls
from .fleet import AgentView, ComputerView
from .review import REVIEW_REPLY

# the credential each kind of channel takes, by the name its option carries
# without `_env`; the value crosses beside the configuration, never in it
SECRETS = {"telegram": "token", "lark": "app_secret", "wecom": "secret"}

_FIELD = re.compile(r"(channel|runtime)-(\d+)-(.+)")
_ENV = re.compile(r"env-(\d+)-(name|value|was)")
# what a runtime card shows and always sends
_RUNTIME_SHOWN = ("kind", "model", "effort", "sandbox_mode", "network_access", "env")


@dataclass(frozen=True, slots=True)
class Held:
    """The agents the computer runs, as their configuration holds them; or
    why that is not known: `listed`, `offline`, `silent` or `refused`."""

    agents: tuple[Mapping[str, Any], ...]
    answer: str
    code: str | None = None

    def agent(self, agent_id: str) -> Mapping[str, Any] | None:
        """One agent's configuration, as the computer holds it."""

        return next((agent for agent in self.agents if agent["id"] == agent_id), None)


async def held(controls: Controls, computer_id: str, *, online: bool) -> Held:
    answer = await controls.outcome(computer_id, {"read": "agents"}, online=online)
    if isinstance(answer, str):
        word, _, code = answer.partition(":")
        return Held((), word, code or None)
    return Held(tuple(answer["agents"]), "listed")


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


@dataclass(frozen=True, slots=True)
class Origins:
    """Where each card of a form stood in the agent it was changing, by
    family; nothing for a card added on the page."""

    channel: tuple[int | None, ...]
    runtime: tuple[int | None, ...]


def configuration(
    form: Mapping[str, str],
    kept: Mapping[str, Any] | None = None,
) -> tuple[
    dict[str, Any], dict[str, dict[str, str]], dict[str, dict[str, str]], Origins
]:
    """The agent the form describes, in the shape the configuration file
    holds it in; the credentials typed into it by channel position; and the
    environment values typed into it by runtime position, which the node
    keeps the way it keeps a credential; and which kept card each card came
    from, so a form shown again keeps saying so.
    Cards are numbered as they were added, gaps and all; they keep their
    order. A credential left empty is not sent: nothing changes.

    Changing an agent that is `kept`, a card that came from it names where
    it stood (`was`): what the page does not show - the variable a
    credential lives in, options only the file carries - stays as it was,
    and what the page shows is taken from the form. An environment row
    that was there and got no new value keeps the variable it was given.
    """

    cards: dict[str, dict[int, dict[str, str]]] = {"channel": {}, "runtime": {}}
    for key, value in form.items():
        if match := _FIELD.fullmatch(key):
            family, number, field = match.groups()
            cards[family].setdefault(int(number), {})[field] = value
    channels: list[dict[str, Any]] = []
    secrets: dict[str, dict[str, str]] = {}
    origins: dict[str, list[int | None]] = {"channel": [], "runtime": []}
    for _, fields in sorted(cards["channel"].items()):
        kind = fields.pop("kind")
        origin = fields.pop("was", None)
        origins["channel"].append(int(origin) if origin else None)
        secret = fields.pop("secret", "")
        if secret:
            secrets[str(len(channels))] = {SECRETS[kind]: secret}
        channel = {
            key: value
            for key, value in _was(kept, "channel", origin).items()
            if key not in fields
        }
        channels.append(
            {**channel, "kind": kind, **{k: v for k, v in fields.items() if v}}
        )
    runtimes: list[dict[str, Any]] = []
    values: dict[str, dict[str, str]] = {}
    for _, fields in sorted(cards["runtime"].items()):
        origin = fields.pop("was", None)
        origins["runtime"].append(int(origin) if origin else None)
        was = _was(kept, "runtime", origin)
        env: dict[int, dict[str, str]] = {}
        for field in list(fields):
            if match := _ENV.fullmatch(field):
                env.setdefault(int(match[1]), {})[match[2]] = fields.pop(field)
        named: dict[str, str] = {}
        typed: dict[str, str] = {}
        for _, row in sorted(env.items()):
            name = row.get("name")
            if not name:
                continue
            if row.get("value"):
                typed[name] = row["value"]
            elif row.get("was") in was.get("env", {}):
                named[name] = was["env"][row["was"]]
        if typed:
            values[str(len(runtimes))] = typed
        runtime: dict[str, Any] = {
            "kind": fields["kind"],
            "sandbox_mode": fields["sandbox_mode"],
            "network_access": fields.get("network_access") == "on",
            "env": named,
        }
        for key in ("model", "effort"):
            if fields.get(key):
                runtime[key] = fields[key]
        # the options only the file carries, kept; what the card shows is
        # the card's
        runtimes.append(
            {
                **{
                    key: value
                    for key, value in was.items()
                    if key not in {*_RUNTIME_SHOWN, *runtime}
                },
                **runtime,
            }
        )
    return (
        {
            **({"id": kept["id"]} if kept else {}),
            "name": form.get("name", ""),
            "mode": form.get("mode", "session"),
            "idle_timeout": _number(form.get("idle_timeout", "0")),
            "channel": channels,
            "runtime": runtimes,
        },
        secrets,
        values,
        Origins(tuple(origins["channel"]), tuple(origins["runtime"])),
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


async def reply_of(controls: Controls, agent: AgentView) -> str | None:
    """What the agent tells those waiting; nothing when the computer
    cannot say."""

    answer = await controls.outcome(
        agent.computer_id,
        {"read": "setting", "agent_id": agent.id, "key": REVIEW_REPLY},
        online=agent.status != "offline",
    )
    return None if isinstance(answer, str) else answer["value"] or ""


async def save(
    controls: Controls,
    agent: AgentView,
    configuration: Mapping[str, Any],
    secrets: Mapping[str, Mapping[str, str]],
    env: Mapping[str, Mapping[str, str]],
    reply: str,
) -> str | None:
    """Change the agent as the page has it, and what it tells those
    waiting; the word for why not when the computer did not."""

    if lacking := _lacking(configuration):
        return lacking
    online = agent.status != "offline"
    written = await controls.outcome(
        agent.computer_id,
        {
            "write": "agent",
            "agent": dict(configuration),
            "secrets": dict(secrets),
            "env": dict(env),
            # empty is a value too: it stops the reply
            "settings": {REVIEW_REPLY: reply},
        },
        online=online,
    )
    return written if isinstance(written, str) else None


async def remove(controls: Controls, agent: AgentView) -> str | None:
    """Let the agent go from its computer; the word for why not."""

    answer = await controls.outcome(
        agent.computer_id,
        {"remove": "agent", "agent_id": agent.id},
        online=agent.status != "offline",
    )
    return answer if isinstance(answer, str) else None


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class Skills:
    """What an agent found to do, by where it found it: `workspace`,
    `personal`, `system`; or why the computer did not say."""

    groups: tuple[tuple[str, tuple[Skill, ...]], ...]
    answer: str
    code: str | None = None


async def skills(controls: Controls, agent: AgentView) -> Skills:
    answer = await controls.outcome(
        agent.computer_id,
        {"read": "skills", "agent_id": agent.id},
        online=agent.status != "offline",
    )
    if isinstance(answer, str):
        word, _, code = answer.partition(":")
        return Skills((), word, code or None)
    return Skills(
        tuple(
            (
                source,
                tuple(
                    Skill(item["name"], item["description"])
                    for item in answer["skills"]
                    if item["source"] == source
                ),
            )
            for source in ("workspace", "personal", "system")
        ),
        "listed",
    )


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    # where it is, relative to the workspace
    path: str
    directory: bool
    size_bytes: int
    modified_at_ms: int


@dataclass(frozen=True, slots=True)
class Workspace:
    entries: tuple[Entry, ...]
    answer: str
    code: str | None = None
    # the directory listed, relative to the workspace; and whether it holds
    # more than a listing does
    at: str = ""
    more: bool = False


async def workspace(controls: Controls, agent: AgentView, path: str = "") -> Workspace:
    """One directory of the agent's workspace, as the computer lists it:
    the top, or the one at `path` relative to it."""

    answer = await controls.outcome(
        agent.computer_id,
        {"read": "workspace", "agent_id": agent.id, "path": path},
        online=agent.status != "offline",
    )
    if isinstance(answer, str):
        word, _, code = answer.partition(":")
        return Workspace((), word, code or None)
    at = answer["at"]
    return Workspace(
        tuple(
            Entry(
                item["name"],
                f"{at}/{item['name']}" if at else item["name"],
                item["kind"] == "directory",
                item["size_bytes"],
                item["modified_at_ms"],
            )
            for item in answer["entries"]
        ),
        "listed",
        at=at,
        more=answer["more"],
    )


def _was(
    kept: Mapping[str, Any] | None, family: str, index: str | None
) -> Mapping[str, Any]:
    """The card a form card came from, when it came from one."""

    if kept is None or not index:
        return {}
    cards = kept[family]
    return cards[int(index)] if int(index) < len(cards) else {}


def _number(text: str) -> float | str:
    """A number typed into the form; anything else goes as typed, for the
    computer to say what is wrong with it."""

    try:
        return float(text or 0)
    except ValueError:
        return text


__all__ = [
    "SECRETS",
    "Entry",
    "Held",
    "Kind",
    "Kinds",
    "Model",
    "Models",
    "Origins",
    "Skill",
    "Skills",
    "Workspace",
    "configuration",
    "create",
    "held",
    "kinds",
    "models",
    "remove",
    "reply_of",
    "save",
    "skills",
    "workspace",
]
