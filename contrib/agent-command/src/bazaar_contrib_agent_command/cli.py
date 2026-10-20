"""Agent configuration and local admission-review commands."""

from __future__ import annotations

import asyncio
import tomllib
from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Literal

import click
from bazaar_contrib_sidecar import Client, Construct, Symbol
from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

if TYPE_CHECKING:
    from .management import AgentManagement


class Option(BaseModel):
    scope: Literal["agent", "channel", "harness"]
    path: str = Field(
        pattern=r"^(?:[0-9]+\.)?[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*$"
    )
    value: Any

    @model_validator(mode="before")
    @classmethod
    def parse(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            path, raw = value.split("=", 1)
            scope, path = path.split(".", 1)
        except ValueError as error:
            raise ValueError("Use SCOPE.KEY=VALUE") from error
        if scope == "agent" and path == "id":
            raise ValueError("Agent IDs are generated and remain stable")
        if scope != "agent" and path.split(".")[-1] == "kind":
            raise ValueError(f"Use --{scope} to select a provider")
        if scope == "harness" and path.split(".")[-1] == "env":
            name, equals, source = raw.partition("=")
            if not equals or not name or not source:
                raise ValueError("Use harness.env=NAME=SOURCE_NAME")
            parsed = {name: source}
        else:
            try:
                parsed = tomllib.loads(f"value = {raw}")["value"]
            except tomllib.TOMLDecodeError:
                parsed = raw
        return {"scope": scope, "path": path, "value": parsed}


def parameters(
    current: dict[str, Any],
    *,
    name: str | None,
    channel: tuple[str, ...],
    harness: tuple[str, ...],
    options: tuple[str, ...],
) -> dict[str, Any]:
    try:
        settings = TypeAdapter(tuple[Option, ...]).validate_python(options)
    except ValidationError as error:
        raise click.BadParameter(str(error), param_hint="--set") from error
    values = deepcopy(current)
    if name is not None:
        values["name"] = name
    for scope, selected in (("channel", channel), ("harness", harness)):
        if selected:
            existing = list(values.get(scope, ()))
            providers = []
            for kind in selected:
                match = next(
                    (
                        index
                        for index, item in enumerate(existing)
                        if item["kind"] == kind
                    ),
                    None,
                )
                providers.append(
                    existing.pop(match) if match is not None else {"kind": kind}
                )
            values[scope] = providers
    seen = set()
    for option in settings:
        identity = option.scope, option.path
        if identity in seen and option.path.split(".")[-1] != "env":
            raise click.BadParameter(
                f"duplicate --set option: {option.scope}.{option.path}"
            )
        seen.add(identity)
        names = option.path.split(".")
        target = values
        if option.scope != "agent":
            providers = values.get(option.scope, ())
            position = int(names.pop(0)) if names[0].isdecimal() else 0
            if position >= len(providers):
                raise click.BadParameter(
                    f"{option.scope} provider position {position} is not configured"
                )
            target = providers[position]
        for key in names[:-1]:
            target = target.setdefault(key, {})
        key = names[-1]
        if key == "env" and option.scope == "harness":
            target.setdefault(key, {}).update(option.value)
        else:
            target[key] = option.value
    return values


def table(rows: Iterable[tuple[str, ...]], headers: tuple[str, ...]) -> str:
    rows = list(rows)
    widths = [max(map(len, column)) for column in zip(headers, *rows, strict=True)]
    return "\n".join(
        "  ".join(
            value.ljust(width) for value, width in zip(row, widths, strict=True)
        ).rstrip()
        for row in (headers, tuple("-" * width for width in widths), *rows)
    )


def create_commands(service: AgentManagement, client: Client) -> click.Group:
    templates = Environment(
        loader=PackageLoader("bazaar_contrib_agent_command", "resources"),
        undefined=StrictUndefined,
        autoescape=False,
        auto_reload=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    templates.filters["table"] = table

    async def execute(
        operation: Awaitable[Any],
        template: str,
        *,
        rows: Callable[[Any], Iterable[tuple[str, ...]]] | None = None,
        err: bool = False,
        **context: object,
    ):
        try:
            result = await operation
            output = await asyncio.to_thread(
                lambda: templates.get_template(template).render(
                    result=result, rows=rows(result) if rows else (), **context
                )
            )
            await asyncio.to_thread(click.echo, output.rstrip("\n"), err=err)
        except click.ClickException:
            raise
        except Exception as error:
            raise click.ClickException(str(error)) from error

    @click.group("agent", help="Manage agents")
    def agent():
        pass

    @agent.command("list", help="List agents")
    def list_agents():
        return execute(
            service.read(),
            "agents.tpl",
            rows=lambda result: (
                (
                    agent.id,
                    agent.name,
                    ", ".join(provider.kind for provider in agent.channel),
                    ", ".join(provider.kind for provider in agent.harness),
                )
                for agent in result.agents
            ),
        )

    @agent.command("add", help="Add an agent")
    @click.option("--name", required=True, metavar="NAME", help="The name of the agent")
    @click.option(
        "--channel",
        multiple=True,
        required=True,
        type=click.Choice(tuple(service.channels)),
        help="The channel providers to use",
    )
    @click.option(
        "--harness",
        multiple=True,
        required=True,
        type=click.Choice(tuple(service.harnesses)),
        help="The harness providers to use",
    )
    @click.option(
        "--set",
        "options",
        multiple=True,
        metavar="SCOPE.KEY=VALUE",
        help="Override an agent, channel, or harness setting",
    )
    def add(name, channel, harness, options):
        values = parameters(
            {}, name=name, channel=channel, harness=harness, options=options
        )
        return execute(
            service.add(values), "agent_changed.tpl", action="Added", err=True
        )

    @agent.command("edit", help="Update an agent")
    @click.argument("selector", metavar="AGENT")
    @click.option("--name", metavar="NAME", help="The name of the agent")
    @click.option(
        "--channel",
        multiple=True,
        type=click.Choice(tuple(service.channels)),
        help="The channel providers to use, replacing the current list",
    )
    @click.option(
        "--harness",
        multiple=True,
        type=click.Choice(tuple(service.harnesses)),
        help="The harness providers to use, replacing the current list",
    )
    @click.option(
        "--set",
        "options",
        multiple=True,
        metavar="SCOPE.KEY=VALUE",
        help="Override an agent, channel, or harness setting",
    )
    def edit(selector, name, channel, harness, options):
        async def update():
            snapshot = await service.read()
            current = service.select(snapshot.agents, selector)
            values = parameters(
                current.model_dump(mode="python"),
                name=name,
                channel=channel,
                harness=harness,
                options=options,
            )
            return await service.edit(
                selector, values, expected_revision=snapshot.revision
            )

        return execute(update(), "agent_changed.tpl", action="Updated", err=True)

    @agent.command("remove", help="Remove an agent")
    @click.argument("selector", metavar="AGENT")
    def remove(selector):
        return execute(
            service.remove(selector), "agent_changed.tpl", action="Removed", err=True
        )

    @agent.group("review", help="Review conversations")
    def review():
        pass

    async def contact_review(
        agent_id: str | None, decision: str | None, thread_id: str | None = None
    ):
        collaboration = await client.context.call(
            "accept", Symbol("bazaar_contrib_collaboration:COLLABORATION")
        )
        if decision is not None:
            snapshot = await service.read()
            messages = await client.context.call(
                "accept", Symbol("bazaar_contrib_messages:MESSAGES")
            )
            thread = Construct("bazaar_contrib_messages:Thread", {"id": thread_id})
            for agent in snapshot.agents:
                storage = await messages.call(
                    "storage",
                    Construct("bazaar_contrib_messages:Agent", {"id": agent.id}),
                )
                if await storage.call("get_thread", thread) is not None:
                    agent_id = agent.id
                    break
            else:
                raise ValueError(f"conversation not found: {thread_id}")
        owner = Construct("bazaar_contrib_messages:Agent", {"id": agent_id})
        contacts = await collaboration.call("for_agent", owner)
        if decision is None:
            messages = await contacts.get("messages")
            inbox = await messages.call(
                "check_inbox",
                owner,
                pending_only=False,
                limit=None,
                review=Construct(
                    "bazaar_contrib_messages.states:Review", {"value": "pending"}
                ),
            )
            rows = []
            for target in await inbox.get("targets"):
                rows.append(
                    {
                        key: await target.get(key)
                        for key in ("thread_id", "channel", "target", "review")
                    }
                )
            return rows
        session = await contacts.call(
            "review_contact",
            Construct("bazaar_contrib_messages:Thread", {"id": thread_id}),
            Construct("bazaar_contrib_messages.states:Review", {"value": decision}),
        )
        return await session.get("review")

    @review.command("list", help="List pending conversations")
    @click.argument("agent_id")
    def pending(agent_id):
        return execute(
            contact_review(agent_id, None),
            "agent_reviews.tpl",
            rows=lambda result: (
                (target["thread_id"], target["channel"], target["target"])
                for target in result
            ),
        )

    @review.command("approve", help="Approve a conversation")
    @click.argument("thread_id")
    def approve(thread_id):
        return execute(
            contact_review(None, "approved", thread_id),
            "agent_reviewed.tpl",
            thread_id=thread_id,
            err=True,
        )

    @review.command("deny", help="Deny a conversation")
    @click.argument("thread_id")
    def deny(thread_id):
        return execute(
            contact_review(None, "denied", thread_id),
            "agent_reviewed.tpl",
            thread_id=thread_id,
            err=True,
        )

    return agent
