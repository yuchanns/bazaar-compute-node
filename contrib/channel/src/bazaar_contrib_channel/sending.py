"""Execute channel operations with one shared delivery and retry policy."""

from __future__ import annotations

import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import uuid7

from bazaar_contrib_timer import Timer, wait_for
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .errors import (
    ChannelConnectionError,
    ChannelDeliveryState,
    ChannelError,
    ChannelSendPhase,
    ChannelTimeoutError,
)
from .models import ChannelDeliveryReceipt, ChannelSendRequest

if TYPE_CHECKING:
    from .contracts import Channel


class ChannelRetryPolicy(BaseModel):
    """Give preparation and every upstream attempt their own timeout."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout: float = Field(default=30, gt=0, allow_inf_nan=False)
    max_attempts: int = Field(default=3, ge=1)
    initial_backoff: float = Field(default=0.25, ge=0, allow_inf_nan=False)
    maximum_backoff: float = Field(default=4, ge=0, allow_inf_nan=False)


@dataclass(frozen=True, slots=True)
class ChannelSendAttempt:
    """Immutable inputs for one attempt; failures report ChannelErrorDetail facts.

    The id stays unchanged across retries of the same operation. Adapters may
    pass it upstream only where the platform supports an idempotency key.
    """

    id: str
    number: int
    timeout: float


@dataclass(frozen=True, slots=True)
class ChannelSendPart:
    """One upstream operation, without retry or backoff in its callback.

    Upload callbacks can retain their result for subsequent send callbacks.
    Each attempt opens and closes its own file or stream; completed uploads
    stay available to later operations without buffering file contents here.
    Message contains the actual visible part and reuses the request attachment
    paths; None denotes an upload only.
    Only message operations represent delivery to the recipient. Idempotence
    requires an upstream guarantee covering this operation and its stable id.
    """

    send: Callable[[ChannelSendAttempt], Awaitable[ChannelDeliveryReceipt | None]]
    message: ChannelSendRequest | None
    idempotent: bool = False

    @property
    def kind(self) -> Literal["message", "upload"]:
        return "message" if self.message is not None else "upload"


@dataclass(frozen=True, slots=True)
class ChannelMessageDelivery:
    """One visible message and its receipt, ready for durable storage."""

    id: str
    index: int
    message: ChannelSendRequest
    receipt: ChannelDeliveryReceipt


class ChannelSender:
    """Own attempt timeouts, backoff and receipts for every channel implementation."""

    def __init__(self, channel: Channel, timer: Timer, *, policy: ChannelRetryPolicy):
        self.channel = channel
        self.timer = timer
        self.policy = policy

    async def send(
        self,
        request: ChannelSendRequest,
        *,
        on_message: Callable[[ChannelMessageDelivery], Awaitable[None]] | None = None,
    ) -> ChannelDeliveryReceipt:
        records: list[JsonValue] = []
        accepted: list[ChannelDeliveryReceipt] = []
        error: ChannelError | None = None
        terminal: ChannelDeliveryReceipt | None = None
        try:
            parts = await wait_for(
                self.timer,
                self.channel.prepare_send(request),
                math.ceil(self.policy.timeout * 1000),
            )
        except Exception as cause:  # noqa: BLE001
            error = self._error(cause, phase="prepare", delivery="not_sent")
            parts = ()
        if error is None and not parts:
            error = ChannelError(
                "channel prepared no delivery operations",
                phase="prepare",
                delivery="not_sent",
            )

        for index, part in enumerate(parts):
            id = str(uuid7())
            attempts: list[JsonValue] = []
            uncertain = False
            backoff = min(self.policy.initial_backoff, self.policy.maximum_backoff)
            receipt: ChannelDeliveryReceipt | None = None
            for number in range(1, self.policy.max_attempts + 1):
                attempt = ChannelSendAttempt(
                    id=id,
                    number=number,
                    timeout=self.policy.timeout,
                )
                try:
                    receipt = await wait_for(
                        self.timer,
                        part.send(attempt),
                        math.ceil(self.policy.timeout * 1000),
                    )
                    if part.kind == "message" and receipt is None:
                        raise ChannelError(
                            "channel send returned no delivery receipt",
                            phase="send",
                            delivery="unknown",
                        )
                except Exception as cause:  # noqa: BLE001
                    delivery: ChannelDeliveryState = (
                        "not_sent" if part.kind == "upload" else "unknown"
                    )
                    error = self._error(
                        cause,
                        phase="upload" if part.kind == "upload" else "send",
                        delivery=delivery,
                    )
                    if part.kind == "upload":
                        error.detail = error.detail.model_copy(
                            update={"delivery": "not_sent"}
                        )
                    uncertain |= error.detail.delivery == "unknown"
                    attempts.append(
                        {
                            "number": number,
                            "error": error.detail.model_dump(mode="json"),
                        }
                    )
                    if (
                        not error.detail.retryable
                        or (uncertain and not part.idempotent)
                        or number == self.policy.max_attempts
                    ):
                        break
                    delay = max(
                        error.detail.retry_after or 0,
                        random.uniform(0, backoff),
                    )
                    await self.timer.create(math.ceil(delay * 1000)).wait()
                    backoff = min(self.policy.maximum_backoff, backoff * 2)
                else:
                    error = None
                    attempts.append(
                        {
                            "number": number,
                            "status": receipt.status if receipt else "uploaded",
                        }
                    )
                    break
            record: dict[str, JsonValue] = {
                "index": index,
                "id": id,
                "kind": part.kind,
                "attempts": attempts,
            }
            if receipt is not None:
                record["receipt"] = receipt.model_dump(mode="json")
            records.append(record)
            if error is not None:
                if uncertain and error.detail.delivery != "unknown":
                    error.detail = error.detail.model_copy(
                        update={"delivery": "unknown"}
                    )
                break
            if receipt is not None:
                if uncertain and receipt.status not in {"confirmed", "queued"}:
                    receipt = receipt.model_copy(update={"status": "unknown"})
                if part.message is not None and on_message is not None:
                    # Persistence failures must never replay an accepted operation.
                    await on_message(
                        ChannelMessageDelivery(
                            id=id, index=index, message=part.message, receipt=receipt
                        )
                    )
                if part.kind == "message" and receipt.status in {"confirmed", "queued"}:
                    accepted.append(receipt)
                elif receipt.status not in {"confirmed", "queued"}:
                    terminal = receipt
                    break

        last = terminal or (accepted[-1] if accepted else None)
        metadata = dict(last.receipt) if last else {}
        metadata["parts"] = records
        if error is not None:
            metadata["error"] = error.detail.model_dump(mode="json")
            status = (
                "unknown"
                if error.detail.delivery == "unknown"
                else "partial"
                if accepted
                else "failed"
            )
        elif terminal is not None:
            status = terminal.status
            if status == "failed" and accepted:
                status = "partial"
        elif accepted:
            status = (
                "queued"
                if any(item.status == "queued" for item in accepted)
                else "confirmed"
            )
        else:
            status = "failed"
        if (
            accepted
            and last is not None
            and not (last.provider_message_id or last.provider_receipt_ref)
        ):
            last = accepted[-1]
        return ChannelDeliveryReceipt(
            status=status,
            provider_message_id=last.provider_message_id if last else None,
            provider_receipt_ref=last.provider_receipt_ref if last else None,
            provider_thread_id=last.provider_thread_id if last else None,
            error=str(error) if error else terminal.error if terminal else None,
            error_kind=error.detail.kind
            if error
            else terminal.error_kind
            if terminal
            else None,
            receipt=metadata,
        )

    @staticmethod
    def _error(
        cause: Exception, *, phase: ChannelSendPhase, delivery: ChannelDeliveryState
    ) -> ChannelError:
        if isinstance(cause, ChannelError):
            return cause
        if isinstance(cause, TimeoutError):
            return ChannelTimeoutError(
                str(cause) or "channel operation timed out",
                phase=phase,
                delivery=delivery,
            )
        if isinstance(cause, ConnectionError):
            return ChannelConnectionError(str(cause), phase=phase, delivery=delivery)
        return ChannelError(
            str(cause) or type(cause).__name__, phase=phase, delivery=delivery
        )
