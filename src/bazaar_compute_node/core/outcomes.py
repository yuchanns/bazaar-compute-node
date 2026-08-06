from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ProviderCallStatus(StrEnum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderCallResult[ResultT]:
    """Provider outcome that never treats an unconfirmed call as a success."""

    status: ProviderCallStatus
    value: ResultT | None = None
    error_kind: str | None = None
    error_message: str | None = None
    receipt: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status is ProviderCallStatus.CONFIRMED:
            if self.value is None:
                raise ValueError("a confirmed provider call requires a value")
            if self.error_kind is not None or self.error_message is not None:
                raise ValueError("a confirmed provider call cannot contain an error")
            return

        if self.value is not None:
            raise ValueError("an unconfirmed provider call cannot contain a value")
        if not self.error_kind:
            raise ValueError("an unconfirmed provider call requires an error_kind")
