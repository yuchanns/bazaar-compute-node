"""Shared upstream error facts, independent of a channel's transport."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

type ChannelSendPhase = Literal["prepare", "connect", "upload", "send", "acknowledge"]
type ChannelDeliveryState = Literal["not_sent", "unknown"]


class ChannelErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    message: str
    phase: ChannelSendPhase
    delivery: ChannelDeliveryState
    retryable: bool
    code: str | None = None
    retry_after: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ChannelError(Exception):
    """Preserve upstream information and facts needed by the common policy.

    An upstream-specific error can use this base type directly. Retryability
    describes the cause; delivery uncertainty separately controls safe replay.
    """

    kind: ClassVar[str] = "channel_error"
    transient: ClassVar[bool] = False
    rejected: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        phase: ChannelSendPhase,
        delivery: ChannelDeliveryState | None = None,
        retryable: bool | None = None,
        code: str | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.detail = ChannelErrorDetail(
            kind=self.kind,
            message=message,
            phase=phase,
            delivery=delivery
            or (
                "not_sent"
                if self.rejected or phase in {"prepare", "connect", "upload"}
                else "unknown"
            ),
            retryable=self.transient if retryable is None else retryable,
            code=code,
            retry_after=retry_after,
        )


class ChannelConnectionError(ChannelError):
    """A transport connection failed; adapters specify whether it wrote data."""

    kind = "channel_connection"
    transient = True


class ChannelTimeoutError(ChannelError):
    """A deadline expired at the recorded stage of an operation."""

    kind = "channel_timeout"
    transient = True


class ChannelRateLimitError(ChannelError):
    """The upstream applied a rate limit, optionally with a retry delay."""

    kind = "channel_rate_limit"
    transient = True
    rejected = True


class ChannelRejectedError(ChannelError):
    """The upstream explicitly rejected an operation."""

    kind = "channel_rejected"
    rejected = True


class ChannelUnavailableError(ChannelError):
    """No running channel of the agent speaks for this conversation or bot."""

    kind = "channel_unavailable"


class ChannelAuthenticationError(ChannelError):
    """The upstream requires corrected credentials or permissions."""

    kind = "channel_authentication"
    rejected = True
