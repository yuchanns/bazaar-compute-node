from __future__ import annotations

import hashlib
import os
from pathlib import Path
from time import time_ns
from uuid import uuid4

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.identity import TelegramThreadIdentity
from bazaar_compute_node.contrib.telegram.outbound import TelegramOutboundChannel
from bazaar_compute_node.contrib.telegram.plugin import TelegramBuilder
from bazaar_compute_node.core.channel import (
    ChannelApprovalRequest,
    ChannelContext,
    ChannelIdentity,
    ChannelSendRequest,
)
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChannelTargetKind,
    FreshCheckState,
    OutboundAttachment,
    OutboundDeliveryState,
    OutboundMessage,
)
from bazaar_compute_node.core.outcomes import ProviderCallStatus

pytestmark = pytest.mark.e2e

_TELEGRAM_STARTUP_TIMEOUT_SECONDS = 60


async def _referenced_paths() -> set[str]:
    return set()


def _required_int(name: str) -> int:
    value = os.environ.get(name)
    if value is None:
        pytest.skip(f"{name} is required for Telegram provider verification")
    try:
        return int(value)
    except ValueError as error:
        raise AssertionError(f"{name} must be an integer") from error


def _channel(tmp_path: Path) -> TelegramOutboundChannel:
    if not os.environ.get("BCN_TELEGRAM_BOT_TOKEN"):
        pytest.skip(
            "BCN_TELEGRAM_BOT_TOKEN is required for Telegram provider verification"
        )
    channel = TelegramBuilder().build(
        ChannelContext(
            agent_id="agent-telegram-e2e",
            attachments=AttachmentMaterializer(lambda: tmp_path, _referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        )
    )
    assert isinstance(channel, TelegramOutboundChannel)
    return channel


def _attachment(tmp_path: Path, name: str) -> OutboundAttachment:
    content = f"Bazaar Compute Node Telegram Task 3C {uuid4()}\n".encode()
    path = tmp_path / name
    path.write_bytes(content)
    return OutboundAttachment(
        name=name,
        relative_path=name,
        media_type="text/plain",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _send_request(
    *,
    identity: TelegramThreadIdentity,
    target_kind: ChannelTargetKind,
    body: str,
    attachments: tuple[OutboundAttachment, ...] = (),
    reply_to_message_id: int | None = None,
) -> ChannelSendRequest:
    nonce = str(uuid4())
    return ChannelSendRequest(
        outbound=OutboundMessage(
            outbound_message_id=f"outbound-{nonce}",
            command_id=f"command-{nonce}",
            session_id=f"session-{nonce}",
            channel_session_id=identity.channel_session_id,
            target=f"{target_kind.value}:{identity.channel_session_id}",
            body=body,
            attachments=attachments,
            state=OutboundDeliveryState.PENDING,
            fresh_check_state=FreshCheckState.PASSED,
            created_at_ms=time_ns() // 1_000_000,
        ),
        target_kind=target_kind,
        provider_thread_id=identity.provider_thread_id,
        provider_reply_to_message_id=(
            str(reply_to_message_id) if reply_to_message_id is not None else None
        ),
    )


@pytest.mark.asyncio
async def test_telegram_real_provider_dm_rich_markdown_and_attachment(
    tmp_path: Path,
) -> None:
    chat_id = _required_int("BCN_TELEGRAM_E2E_DM_CHAT_ID")
    channel = _channel(tmp_path)
    await channel.start(timeout=_TELEGRAM_STARTUP_TIMEOUT_SECONDS)
    try:
        bot_id = channel.health["bot_id"]
        assert isinstance(bot_id, int)
        bot_username = channel.health["bot_username"]
        assert isinstance(bot_username, str)
        assert channel.get_identity() == ChannelIdentity(
            id=str(bot_id),
            name=bot_username,
        )
        identity = TelegramThreadIdentity(bot_id=bot_id, chat_id=chat_id, topic_id=0)
        nonce = uuid4()
        result = await channel.send(
            _send_request(
                identity=identity,
                target_kind=ChannelTargetKind.DM,
                body=(
                    "## BCN Telegram provider verification\n\n"
                    "- DM route\n"
                    "- **Rich Markdown**\n"
                    "- document follows\n\n"
                    f"Run: `{nonce}`"
                ),
                attachments=(_attachment(tmp_path, "telegram-dm-e2e.txt"),),
            ),
            timeout=60,
        )
        assert result.status is ProviderCallStatus.CONFIRMED
        assert result.receipt["total_parts"] == 2
        assert result.receipt["confirmed_parts"] == 2
    finally:
        await channel.stop(timeout=5)
    assert channel.get_identity() is None


@pytest.mark.asyncio
async def test_telegram_real_provider_group_topic_reply_and_attachment(
    tmp_path: Path,
) -> None:
    chat_id = _required_int("BCN_TELEGRAM_E2E_GROUP_CHAT_ID")
    topic_id = _required_int("BCN_TELEGRAM_E2E_TOPIC_ID")
    reply_to_message_id = _required_int("BCN_TELEGRAM_E2E_REPLY_TO_MESSAGE_ID")
    channel = _channel(tmp_path)
    await channel.start(timeout=_TELEGRAM_STARTUP_TIMEOUT_SECONDS)
    try:
        bot_id = channel.health["bot_id"]
        assert isinstance(bot_id, int)
        identity = TelegramThreadIdentity(
            bot_id=bot_id,
            chat_id=chat_id,
            topic_id=topic_id,
        )
        nonce = uuid4()
        result = await channel.send(
            _send_request(
                identity=identity,
                target_kind=ChannelTargetKind.GROUP,
                body=(
                    "## BCN topic reply verification\n\n"
                    "This Rich Markdown response and its attachment belong to the "
                    "same topic.\n\n"
                    f"Run: `{nonce}`"
                ),
                attachments=(_attachment(tmp_path, "telegram-topic-e2e.txt"),),
                reply_to_message_id=reply_to_message_id,
            ),
            timeout=60,
        )
        assert result.status is ProviderCallStatus.CONFIRMED
        assert result.receipt["total_parts"] == 2
        assert result.receipt["confirmed_parts"] == 2
    finally:
        await channel.stop(timeout=5)


@pytest.mark.asyncio
async def test_telegram_real_provider_inline_approval(tmp_path: Path) -> None:
    chat_id = _required_int("BCN_TELEGRAM_E2E_DM_CHAT_ID")
    sender_id = _required_int("BCN_TELEGRAM_E2E_APPROVAL_SENDER_ID")
    channel = _channel(tmp_path)
    await channel.start(timeout=_TELEGRAM_STARTUP_TIMEOUT_SECONDS)
    try:
        bot_id = channel.health["bot_id"]
        assert isinstance(bot_id, int)
        identity = TelegramThreadIdentity(bot_id=bot_id, chat_id=chat_id, topic_id=0)
        request_id = f"approval-{uuid4()}"
        result = await channel.request_approval(
            ChannelApprovalRequest(
                approval=ApprovalRequest(
                    request_id=request_id,
                    session_id=f"session-{uuid4()}",
                    runtime_session_id=f"runtime-{uuid4()}",
                    action="command_execution",
                    created_at_ms=time_ns() // 1_000_000,
                    description="Approve the Task 3C Telegram provider verification.",
                ),
                target_kind=ChannelTargetKind.DM,
                provider_thread_id=identity.provider_thread_id,
                provider_sender_id=str(sender_id),
            ),
            timeout=120,
        )
        assert result.request_id == request_id
        assert result.decision in (
            ApprovalDecision.APPROVED,
            ApprovalDecision.REJECTED,
        )
        assert channel.health["approval_feedback_failures"] == 0
    finally:
        await channel.stop(timeout=5)
