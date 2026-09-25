"""The way in for a management plane: requests it hands the node, in the
node's own command protocol, answered where the node reports to."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .actor import Actors
from .command import ICommandService
from .lifecycle import IAsyncLifecycle, TimeoutBudget
from .observability import IAudit
from .timerwheel import TimerWheel


@dataclass(frozen=True, slots=True)
class AgentCommands:
    """One agent's command service, and who its conversations are answered
    by: what the local command server reaches after it has checked a
    caller, reached here directly, the caller being the node's own control."""

    service: ICommandService
    actors: Actors


# an agent's commands by its id; nothing for an agent that is not running
type CommandsOf = Callable[[str], AgentCommands | None]


class Refused(ValueError):
    """A request the node will not take, with why in its message: it was
    understood and is not for something missing, it is simply not allowed -
    a configuration the node will not run, an agent in no state to change."""


class NodeCommands(Protocol):
    """The node itself, for what only an operator may ask: which agents it
    runs, and what one of them has to work with.

    An agent crosses as the plain mapping its configuration file holds it
    in; the node is what reads one, says what is wrong with it, and hands
    back what it wrote. Credentials go the other way only, as a value under
    a name the node picks, and never come back."""

    def read_agents(self) -> Mapping[str, object]:
        """Every agent the node is configured with, and the kinds of channel
        and runtime installed here for a form to offer."""
        ...

    async def write_agent(
        self,
        agent: Mapping[str, object],
        secrets: Mapping[str, Mapping[str, str]],
        env: Mapping[str, Mapping[str, str]],
        settings: Mapping[str, str],
    ) -> Mapping[str, object]:
        """Take a new agent in, or change one already there - told apart by
        whether the mapping names an agent the node knows."""
        ...

    async def delete_agent(self, agent_id: str) -> Mapping[str, object]:
        """Stop an agent and strike it from the configuration."""
        ...

    async def read_setting(self, agent_id: str, key: str) -> str | None:
        """What one agent is set to do under a key, as its storage keeps it,
        whether or not it is running."""
        ...

    async def read_workspace(self, agent_id: str, path: str) -> Mapping[str, object]:
        """What is in one directory of an agent's workspace:
        the top, or the one at `path` relative to it, never one outside."""
        ...

    async def read_runtimes(self) -> Mapping[str, object]:
        """The runtimes installed here: whether each can be run, and which
        version it is."""
        ...

    async def read_models(self, kind: str) -> Mapping[str, object]:
        """The models one installed runtime will answer as; or none, and
        why it would not say."""
        ...

    async def read_skills(self, agent_id: str) -> Mapping[str, object]:
        """The skills one agent's runtimes found. An agent with nothing
        running has none to tell."""
        ...


@dataclass(frozen=True, slots=True)
class ControlContext:
    """What the node hands a control when it builds one."""

    options: Mapping[str, object]
    timer_wheel: TimerWheel
    timeout_budget: TimeoutBudget
    # where the node reports; what a request was answered with goes there
    audit: IAudit
    commands_of: CommandsOf
    # the node itself, for a request about the agents rather than to one
    node: NodeCommands


class IControl(IAsyncLifecycle, Protocol):
    """A source of requests for the node to run; started once the node can
    answer them, stopped before the sink they are answered through."""

    @property
    def name(self) -> str:
        """Return the stable entry-point identity of this adapter."""
        ...

    @property
    def health(self) -> Mapping[str, object]:
        """Describe how the control is doing, for the node health record."""
        ...


__all__ = [
    "AgentCommands",
    "CommandsOf",
    "ControlContext",
    "IControl",
    "NodeCommands",
    "Refused",
]
