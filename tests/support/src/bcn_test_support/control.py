from __future__ import annotations

from collections.abc import Callable, Mapping
from time import time_ns
from typing import cast

from bazaar_compute_node.core.channel import ChannelSendRequest
from bazaar_compute_node.core.models import (
    Message,
    MessageDirection,
    SenderIdentity,
)

from .channel import TestChannel
from .storage import MemoryStorage

CommandRecord = tuple[str, tuple[str, ...]]


class TestControl:
    """Control surface owned by the test-only provider."""

    def __init__(self, context: Mapping[str, object]) -> None:
        channel = context.get("channel")
        storage = context.get("storage")
        command_log = context.get("command_log")
        is_started = context.get("is_started")
        if not isinstance(command_log, list):
            raise TypeError("test control requires a command log")
        if not callable(is_started):
            raise TypeError("test control requires an is_started callback")
        self._channel = channel
        self._storage = storage
        self._command_log = cast(list[CommandRecord], command_log)
        self._is_started = cast(Callable[[], bool], is_started)

    async def handle(self, request: Mapping[str, object]) -> Mapping[str, object]:
        operation = request.get("operation")
        if operation == "inject":
            if not isinstance(self._channel, TestChannel):
                raise RuntimeError("test control inject requires a TestChannel")
            payload = request.get("message")
            if not isinstance(payload, Mapping):
                raise ValueError("control inject requires a message object")
            message = self._message_from_control(payload)
            await self._channel.inject(message)
            return {
                "accepted": True,
                "session_id": message.session_id,
                "message_id": message.message_id,
            }
        if operation == "status":
            storage = self._storage
            channel = self._channel
            if not isinstance(storage, MemoryStorage):
                raise RuntimeError("test control status requires a MemoryStorage")
            if not isinstance(channel, TestChannel):
                raise RuntimeError("test control status requires a TestChannel")
            return self._status(storage, channel)
        raise ValueError(f"unsupported test control operation: {operation}")

    def _status(
        self,
        storage: MemoryStorage,
        channel: TestChannel,
    ) -> dict[str, object]:
        return {
            "started": self._is_started(),
            "messages": {
                session_id: [_serialize_message(message) for message in messages]
                for session_id, messages in storage.messages.items()
            },
            "cursors": {
                session_id: {
                    "delivered_through_seq": cursor.delivered_through_seq,
                }
                for session_id, cursor in storage.cursors.items()
            },
            "sent_messages": [
                _serialize_channel_send(message) for message in channel.sent_messages
            ],
            "bcc_commands": [
                {"session_id": session_id, "command": list(command)}
                for session_id, command in self._command_log
            ],
        }

    @staticmethod
    def _message_from_control(payload: Mapping[str, object]) -> Message:
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")

        seq = payload.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise ValueError("seq must be a positive integer")
        body = payload.get("body")
        if not isinstance(body, str):
            raise TypeError("body must be text")
        channel_session_id = payload.get("channel_session_id", f"channel-{session_id}")
        message_id = payload.get("message_id", f"test-message-{session_id}-{seq}")
        provider_message_id = payload.get(
            "provider_message_id", f"test-provider-{session_id}-{seq}"
        )
        canonical_target = payload.get("canonical_target", f"#test:{session_id}")
        provider_thread_id = payload.get("provider_thread_id", f"thread-{session_id}")
        received_at_ms = payload.get("received_at_ms", time_ns() // 1_000_000)
        provider_time_ms = payload.get("provider_time_ms", received_at_ms)
        sender = payload.get("sender", "Test")
        message_type = payload.get("message_type", "text")
        channel = payload.get("channel", "test")
        for value, field_name in (
            (channel_session_id, "channel_session_id"),
            (message_id, "message_id"),
            (provider_message_id, "provider_message_id"),
            (canonical_target, "canonical_target"),
            (message_type, "message_type"),
            (channel, "channel"),
            (provider_thread_id, "provider_thread_id"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        if sender is not None and (not isinstance(sender, str) or not sender):
            raise ValueError("sender must be a non-empty string")
        for value, field_name in (
            (received_at_ms, "received_at_ms"),
            (provider_time_ms, "provider_time_ms"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        reply_to_message_id = payload.get("reply_to_message_id")
        if reply_to_message_id is not None and not isinstance(reply_to_message_id, str):
            raise TypeError("reply_to_message_id must be a string")
        return Message(
            direction=MessageDirection.INBOUND,
            seq=seq,
            message_id=cast(str, message_id),
            session_id=session_id,
            channel_session_id=cast(str, channel_session_id),
            channel=cast(str, channel),
            provider_message_id=cast(str, provider_message_id),
            received_at_ms=cast(int, received_at_ms),
            sender=(
                SenderIdentity(id=sender, name=sender)
                if isinstance(sender, str)
                else None
            ),
            message_type=cast(str, message_type),
            target=cast(str, canonical_target),
            body=body,
            provider_time_ms=cast(int, provider_time_ms),
            provider_thread_id=cast(str, provider_thread_id),
            reply_to_message_id=reply_to_message_id,
        )


def _serialize_message(message: Message) -> dict[str, object]:
    return {
        "message_id": message.message_id,
        "seq": message.seq,
        "direction": message.direction.value,
        "command_id": message.command_id,
        "session_id": message.session_id,
        "channel_session_id": message.channel_session_id,
        "target": message.target,
        "body": message.body,
        "attachments": [
            {
                "name": attachment.name,
                "relative_path": attachment.relative_path,
                "media_type": attachment.media_type,
                "size_bytes": attachment.size_bytes,
            }
            for attachment in message.attachments
        ],
        "delivery_state": (
            message.delivery_state.value if message.delivery_state is not None else None
        ),
        "received_at_ms": message.received_at_ms,
        "created_at_ms": message.created_at_ms,
        "provider_message_id": message.provider_message_id,
        "provider_receipt_ref": message.provider_receipt_ref,
        "provider_attempted_at_ms": message.provider_attempted_at_ms,
        "completed_at_ms": message.completed_at_ms,
        "error_kind": message.error_kind,
        "error_message": message.error_message,
    }


def _serialize_channel_send(request: ChannelSendRequest) -> dict[str, object]:
    return {
        "session_id": request.session_id,
        "body": request.body,
        "attachments": [
            {
                "name": attachment.name,
                "relative_path": attachment.relative_path,
                "media_type": attachment.media_type,
                "size_bytes": attachment.size_bytes,
                "sha256": attachment.sha256,
            }
            for attachment in request.attachments
        ],
        "target_kind": request.target_kind.value,
        "provider_thread_id": request.provider_thread_id,
        "provider_reply_to_message_id": request.provider_reply_to_message_id,
    }


__all__ = ["TestControl"]
