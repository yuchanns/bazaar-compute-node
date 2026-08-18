from __future__ import annotations

import asyncio
import math

from ..audit import ErrorKind
from ..channel import ChannelDeliveryReceipt, ChannelSendRequest, IChannel
from ..models import OutboundDeliveryState
from ..outcomes import (
    OutboundDeliveryResult,
    ProviderCallResult,
    ProviderCallStatus,
)


class OutboundDeliveryService:
    """Map one Channel provider call into a provider-neutral delivery result."""

    def __init__(self, channel: IChannel, *, timeout: float) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        self._channel = channel
        self._timeout = float(timeout)

    async def deliver(self, request: ChannelSendRequest) -> OutboundDeliveryResult:
        try:
            provider_result = await self._channel.send(
                request,
                timeout=self._timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            return OutboundDeliveryResult(
                state=OutboundDeliveryState.UNKNOWN,
                error_kind=ErrorKind.PROVIDER_UNKNOWN.value,
                error_message=str(error) or type(error).__name__,
                next_action="reconcile channel delivery before retrying",
            )

        return self._map_provider_result(provider_result)

    @staticmethod
    def _map_provider_result(
        provider_result: ProviderCallResult[ChannelDeliveryReceipt],
    ) -> OutboundDeliveryResult:
        if provider_result.status is ProviderCallStatus.CONFIRMED:
            receipt = provider_result.value
            if not isinstance(receipt, ChannelDeliveryReceipt):
                raise ValueError("confirmed channel delivery has no receipt")
            return OutboundDeliveryResult(
                state=OutboundDeliveryState.SENT,
                provider_message_id=receipt.provider_message_id,
                provider_receipt_ref=receipt.provider_receipt_ref,
                receipt=dict(provider_result.receipt),
            )

        if provider_result.status is ProviderCallStatus.QUEUED:
            receipt = provider_result.value
            if not isinstance(receipt, ChannelDeliveryReceipt):
                raise ValueError("queued channel delivery has no receipt")
            return OutboundDeliveryResult(
                state=OutboundDeliveryState.QUEUED,
                provider_message_id=receipt.provider_message_id,
                provider_receipt_ref=receipt.provider_receipt_ref,
                receipt=dict(provider_result.receipt),
            )

        if provider_result.status is ProviderCallStatus.PARTIAL:
            receipt = provider_result.value
            if not isinstance(receipt, ChannelDeliveryReceipt):
                raise ValueError("partial channel delivery has no receipt")
            return OutboundDeliveryResult(
                state=OutboundDeliveryState.PARTIAL,
                provider_message_id=receipt.provider_message_id,
                provider_receipt_ref=receipt.provider_receipt_ref,
                error_kind=(
                    provider_result.error_kind or ErrorKind.PROVIDER_PARTIAL.value
                ),
                error_message=provider_result.error_message,
                next_action="do not retry the complete message automatically",
                receipt=dict(provider_result.receipt),
            )

        provider_receipt_ref = provider_result.receipt.get("provider_receipt_ref")
        if not isinstance(provider_receipt_ref, str) or not provider_receipt_ref:
            provider_receipt_ref = None
        if provider_result.status is ProviderCallStatus.FAILED:
            return OutboundDeliveryResult(
                state=OutboundDeliveryState.FAILED,
                provider_receipt_ref=provider_receipt_ref,
                error_kind=(
                    provider_result.error_kind or ErrorKind.PROVIDER_FAILED.value
                ),
                error_message=provider_result.error_message,
                receipt=dict(provider_result.receipt),
            )
        return OutboundDeliveryResult(
            state=OutboundDeliveryState.UNKNOWN,
            provider_receipt_ref=provider_receipt_ref,
            error_kind=(provider_result.error_kind or ErrorKind.PROVIDER_UNKNOWN.value),
            error_message=provider_result.error_message,
            next_action="reconcile channel delivery before retrying",
            receipt=dict(provider_result.receipt),
        )
