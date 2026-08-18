from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .models import OutboundDeliveryState


class ProviderCallStatus(StrEnum):
    CONFIRMED = "confirmed"
    QUEUED = "queued"
    PARTIAL = "partial"
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
        if self.status in {
            ProviderCallStatus.CONFIRMED,
            ProviderCallStatus.QUEUED,
        }:
            if self.value is None:
                raise ValueError(
                    f"a {self.status.value} provider call requires a value"
                )
            if self.error_kind is not None or self.error_message is not None:
                raise ValueError(
                    f"a {self.status.value} provider call cannot contain an error"
                )
            return

        if self.status is ProviderCallStatus.PARTIAL:
            if self.value is None:
                raise ValueError("a partial provider call requires a value")
            if not self.error_kind:
                raise ValueError("a partial provider call requires an error_kind")
            return

        if self.value is not None:
            raise ValueError("an unconfirmed provider call cannot contain a value")
        if not self.error_kind:
            raise ValueError("an unconfirmed provider call requires an error_kind")


@dataclass(frozen=True, slots=True)
class OutboundDeliveryResult:
    """Provider-neutral result of one outbound delivery attempt."""

    state: OutboundDeliveryState
    provider_message_id: str | None = None
    provider_receipt_ref: str | None = None
    error_kind: str | None = None
    error_message: str | None = None
    next_action: str | None = None
    receipt: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        terminal_states = (
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.QUEUED,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
        )
        if (
            not isinstance(self.state, OutboundDeliveryState)
            or self.state not in terminal_states
        ):
            raise ValueError("outbound delivery result requires a provider outcome")
        if not isinstance(self.receipt, Mapping):
            raise TypeError("receipt must be a mapping")
        for value, field_name in (
            (self.provider_message_id, "provider_message_id"),
            (self.provider_receipt_ref, "provider_receipt_ref"),
            (self.error_kind, "error_kind"),
            (self.error_message, "error_message"),
            (self.next_action, "next_action"),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field_name} must be non-empty text when present")
        if self.state in {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.QUEUED,
        }:
            if self.error_kind is not None or self.error_message is not None:
                raise ValueError("successful delivery result cannot contain an error")
        elif self.error_kind is None:
            raise ValueError("unconfirmed delivery result requires an error_kind")
