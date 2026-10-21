from __future__ import annotations

from bazaar_contrib_channel import ChannelDeliveryReceipt

from .outcomes import OutboundDeliveryResult, ProviderCallResult, ProviderCallStatus
from .states import ErrorKind, OutboundDeliveryState


def from_receipt(receipt: ChannelDeliveryReceipt) -> OutboundDeliveryResult:
    status = ProviderCallStatus(receipt.status)
    metadata = dict(receipt.receipt)
    if status in {ProviderCallStatus.FAILED, ProviderCallStatus.UNKNOWN}:
        metadata.update(
            {
                key: value
                for key, value in (
                    ("provider_message_id", receipt.provider_message_id),
                    ("provider_receipt_ref", receipt.provider_receipt_ref),
                    ("provider_thread_id", receipt.provider_thread_id),
                )
                if value is not None
            }
        )
    provider_result = ProviderCallResult(
        status=status,
        value=receipt
        if status
        in {
            ProviderCallStatus.CONFIRMED,
            ProviderCallStatus.QUEUED,
            ProviderCallStatus.PARTIAL,
        }
        else None,
        error_kind=(
            receipt.error_kind
            or {
                ProviderCallStatus.PARTIAL: ErrorKind.PROVIDER_PARTIAL.value,
                ProviderCallStatus.FAILED: ErrorKind.PROVIDER_FAILED.value,
                ProviderCallStatus.UNKNOWN: ErrorKind.PROVIDER_UNKNOWN.value,
            }.get(status)
        ),
        error_message=receipt.error,
        receipt=metadata,
    )

    return _map_provider_result(provider_result)


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
            provider_thread_id=receipt.provider_thread_id,
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
            provider_thread_id=receipt.provider_thread_id,
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
            provider_thread_id=receipt.provider_thread_id,
            error_kind=(provider_result.error_kind or ErrorKind.PROVIDER_PARTIAL.value),
            error_message=provider_result.error_message,
            next_action="do not retry the complete message automatically",
            receipt=dict(provider_result.receipt),
        )

    provider_receipt_ref = provider_result.receipt.get("provider_receipt_ref")
    if not isinstance(provider_receipt_ref, str) or not provider_receipt_ref:
        provider_receipt_ref = None
    # an attempt that ended badly can still have left part of itself with
    # the peer, and the receipt is where that shows
    provider_message_id = provider_result.receipt.get("provider_message_id")
    if not isinstance(provider_message_id, str) or not provider_message_id:
        provider_message_id = None
    provider_thread_id = provider_result.receipt.get("provider_thread_id")
    if not isinstance(provider_thread_id, str) or not provider_thread_id:
        provider_thread_id = None
    if provider_result.status is ProviderCallStatus.FAILED:
        return OutboundDeliveryResult(
            state=OutboundDeliveryState.FAILED,
            provider_message_id=provider_message_id,
            provider_receipt_ref=provider_receipt_ref,
            provider_thread_id=provider_thread_id,
            error_kind=(provider_result.error_kind or ErrorKind.PROVIDER_FAILED.value),
            error_message=provider_result.error_message,
            receipt=dict(provider_result.receipt),
        )
    return OutboundDeliveryResult(
        state=OutboundDeliveryState.UNKNOWN,
        provider_message_id=provider_message_id,
        provider_receipt_ref=provider_receipt_ref,
        provider_thread_id=provider_thread_id,
        error_kind=(provider_result.error_kind or ErrorKind.PROVIDER_UNKNOWN.value),
        error_message=provider_result.error_message,
        next_action="reconcile channel delivery before retrying",
        receipt=dict(provider_result.receipt),
    )
