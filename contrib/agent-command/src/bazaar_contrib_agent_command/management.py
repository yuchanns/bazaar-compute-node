"""Manage Agent configuration through its public model and selected carrier."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid7

from bazaar_contrib_agent import AgentConfig, Config
from bazaar_contrib_config import (
    ConfigCandidate,
    ConfigConflict,
    ConfigDocument,
    ConfigSnapshot,
    Configuration,
    ConfigurationApplication,
)
from bazaar_contrib_sidecar import Client
from pydantic import TypeAdapter


@dataclass(frozen=True, slots=True)
class AgentConfiguration:
    agents: tuple[AgentConfig, ...]
    revision: str | None


class AgentManagement:
    """Edit saved Agent entities without acquiring their running instances."""

    def __init__(
        self,
        configuration: Configuration,
        client: Client,
        channels: tuple[str, ...],
        harnesses: tuple[str, ...],
    ):
        self.configuration = configuration
        self.client = client
        self.channels = channels
        self.harnesses = harnesses

    async def _read(self) -> tuple[dict[str, Any], Config, str | None]:
        candidate, snapshot = await self.configuration.read()
        document = ConfigDocument.model_validate(candidate.payload)
        entry = next(
            (
                entry
                for package in document.contribs
                if package.applies
                for entry in package.enabled
                if entry.entry == "agent"
            ),
            None,
        )
        if entry is None:
            raise ValueError("The agent contrib is not enabled")
        return (
            deepcopy(candidate.payload),
            Config.model_validate(entry.config),
            snapshot.revision if snapshot is not None else None,
        )

    async def _save(
        self, payload: dict[str, Any], config: Config, revision: str | None
    ) -> None:
        document = ConfigDocument.model_validate(payload)
        for index, package in enumerate(document.contribs):
            if not package.applies:
                continue
            for position, entry in enumerate(package.enabled):
                if entry.entry == "agent":
                    payload["contribs"][index]["enabled"][position]["config"] = (
                        config.model_dump(mode="json", exclude_none=True)
                    )
        if self.configuration.start is not None:
            try:
                environment = await self.client.call("configuration", "environment")
            except FileNotFoundError, ConnectionRefusedError:
                await self.configuration.commit(payload, expected_revision=revision)
                return
            if environment != (
                self.configuration.environment.model_dump(mode="json")
                if self.configuration.environment is not None
                else None
            ):
                raise ValueError(
                    "the sidecar belongs to another configuration; "
                    "select its configuration or endpoint"
                )
            _, snapshot = TypeAdapter(
                tuple[ConfigCandidate, ConfigSnapshot | None]
            ).validate_python(await self.client.call("configuration", "read"))
            if (snapshot.revision if snapshot is not None else None) != revision:
                raise ConfigConflict(
                    "the sidecar configuration differs from the selected source"
                )
            snapshot = await self.client.call(
                "configuration", "commit", payload, expected_revision=revision
            )
            result = ConfigurationApplication.model_validate(
                await self.client.call("configuration", "apply", snapshot)
            )
        else:
            snapshot = await self.configuration.commit(
                payload, expected_revision=revision
            )
            result = await self.configuration.apply(snapshot)
        if result.error is not None or not result.applied:
            raise ValueError(
                f"configuration saved but not applied: {result.error}"
                if result.error
                else "configuration saved but not applied"
            )

    async def read(self) -> AgentConfiguration:
        _, config, revision = await self._read()
        return AgentConfiguration(config.agents, revision)

    @staticmethod
    def select(agents: tuple[AgentConfig, ...], selector: str) -> AgentConfig:
        matches = [agent for agent in agents if selector in (agent.id, agent.name)]
        if not matches:
            raise ValueError(f"Agent not found: {selector}")
        if len(matches) > 1:
            raise ValueError(f"Agent selector is ambiguous: {selector}")
        return matches[0]

    async def add(self, values: Mapping[str, object]) -> AgentConfig:
        payload, config, revision = await self._read()
        agent = AgentConfig.model_validate({**values, "id": str(uuid7())})
        await self._save(payload, Config(agents=(*config.agents, agent)), revision)
        return agent

    async def edit(
        self,
        selector: str,
        values: Mapping[str, object],
        *,
        expected_revision: str | None,
    ) -> AgentConfig:
        payload, config, _ = await self._read()
        current = self.select(config.agents, selector)
        agent = AgentConfig.model_validate(
            {**current.model_dump(mode="python"), **values, "id": current.id}
        )
        config = Config(
            agents=tuple(
                agent if item.id == current.id else item for item in config.agents
            )
        )
        await self._save(payload, config, expected_revision)
        return agent

    async def remove(self, selector: str) -> AgentConfig:
        payload, config, revision = await self._read()
        agent = self.select(config.agents, selector)
        await self._save(
            payload,
            Config(agents=tuple(item for item in config.agents if item.id != agent.id)),
            revision,
        )
        return agent
