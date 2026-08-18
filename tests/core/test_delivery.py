from __future__ import annotations

import asyncio

import pytest
from bcn_test_support import TestChannel

from bazaar_compute_node.core.channel import (
    ChannelDeliveryReceipt,
    ChannelSendRequest,
)
from bazaar_compute_node.core.models import ChannelTargetKind, OutboundDeliveryState
from bazaar_compute_node.core.orchestration.delivery import OutboundDeliveryService
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus


def _request() -> ChannelSendRequest:
    return ChannelSendRequest(
        session_id="session-1",
        body="hello",
        attachments=(),
        target_kind=ChannelTargetKind.DM,
        provider_thread_id="thread-1",
        provider_reply_to_message_id="message-1",
    )


@pytest.mark.asyncio
async def test_outbound_delivery_service_maps_provider_results() -> None:
    channel = TestChannel()
    await channel.start(timeout=1)
    service = OutboundDeliveryService(channel, timeout=1)

    channel.queue_send_result(
        ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=ChannelDeliveryReceipt(provider_message_id="confirmed-1"),
        )
    )
    confirmed = await service.deliver(_request())
    assert confirmed.state is OutboundDeliveryState.SENT
    assert confirmed.provider_message_id == "confirmed-1"

    channel.queue_send_result(
        ProviderCallResult(
            status=ProviderCallStatus.QUEUED,
            value=ChannelDeliveryReceipt(provider_receipt_ref="queued-1"),
        )
    )
    queued = await service.deliver(_request())
    assert queued.state is OutboundDeliveryState.QUEUED
    assert queued.provider_receipt_ref == "queued-1"

    channel.queue_send_result(
        ProviderCallResult(
            status=ProviderCallStatus.PARTIAL,
            value=ChannelDeliveryReceipt(provider_receipt_ref="partial-1"),
            error_kind="batch_failed",
            error_message="second batch failed",
            receipt={"confirmed_parts": 1},
        )
    )
    partial = await service.deliver(_request())
    assert partial.state is OutboundDeliveryState.PARTIAL
    assert partial.provider_receipt_ref == "partial-1"
    assert partial.error_kind == "batch_failed"
    assert partial.error_message == "second batch failed"
    assert partial.next_action == "do not retry the complete message automatically"
    assert partial.receipt == {"confirmed_parts": 1}

    channel.queue_send_result(
        ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="provider_rejected",
            error_message="provider rejected delivery",
            receipt={"provider_receipt_ref": "failed-1"},
        )
    )
    failed = await service.deliver(_request())
    assert failed.state is OutboundDeliveryState.FAILED
    assert failed.provider_receipt_ref == "failed-1"
    assert failed.error_kind == "provider_rejected"

    channel.queue_send_result(
        ProviderCallResult(
            status=ProviderCallStatus.UNKNOWN,
            error_kind="transport_eof",
            error_message="delivery outcome is unknown",
            receipt={"provider_receipt_ref": "unknown-1"},
        )
    )
    unknown = await service.deliver(_request())
    assert unknown.state is OutboundDeliveryState.UNKNOWN
    assert unknown.provider_receipt_ref == "unknown-1"
    assert unknown.error_kind == "transport_eof"
    assert unknown.next_action == "reconcile channel delivery before retrying"


class _ErrorChannel(TestChannel):
    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        del request, timeout
        raise RuntimeError("connection dropped")


@pytest.mark.asyncio
async def test_outbound_delivery_service_maps_adapter_exception_to_unknown() -> None:
    service = OutboundDeliveryService(_ErrorChannel(), timeout=1)

    result = await service.deliver(_request())

    assert result.state is OutboundDeliveryState.UNKNOWN
    assert result.error_kind == "provider_unknown"
    assert result.error_message == "connection dropped"
    assert result.next_action == "reconcile channel delivery before retrying"


class _CancelledChannel(TestChannel):
    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        del request, timeout
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_outbound_delivery_service_propagates_cancellation() -> None:
    service = OutboundDeliveryService(_CancelledChannel(), timeout=1)

    with pytest.raises(asyncio.CancelledError):
        await service.deliver(_request())
