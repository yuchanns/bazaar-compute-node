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


@dataclass(frozen=True, slots=True)
class ControlContext:
    """What the node hands a control when it builds one."""

    options: Mapping[str, object]
    timer_wheel: TimerWheel
    timeout_budget: TimeoutBudget
    # where the node reports; what a request was answered with goes there
    audit: IAudit
    commands_of: CommandsOf


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


__all__ = ["AgentCommands", "CommandsOf", "ControlContext", "IControl"]
