from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ProviderCallStatus(StrEnum):
    CONFIRMED = "confirmed"
    FAILED = "failed"


class HarnessFailureReason(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION_LOST = "connection_lost"
    INTERRUPTED = "interrupted"
    UNAVAILABLE = "unavailable"
    STATE_MISMATCH = "state_mismatch"
    PROTOCOL = "protocol"
    EXECUTION = "execution"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class HarnessFailure:
    """Why execution failed, independently of whether its result needs recovery."""

    reason: HarnessFailureReason
    message: str
    needs_reconciliation: bool = False
    code: int | str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCallResult[ResultT]:
    """A confirmed call result or a failure with explicit recovery requirements."""

    status: ProviderCallStatus
    value: ResultT | None = None
    failure: HarnessFailure | None = None
    receipt: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status is ProviderCallStatus.CONFIRMED:
            if self.value is None:
                raise ValueError(
                    f"a {self.status.value} provider call requires a value"
                )
            if self.failure is not None:
                raise ValueError(
                    f"a {self.status.value} provider call cannot contain an error"
                )
            return

        if self.value is not None:
            raise ValueError("an unconfirmed provider call cannot contain a value")
        if self.failure is None:
            raise ValueError("a failed provider call requires a failure")
