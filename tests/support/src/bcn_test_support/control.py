from __future__ import annotations

from collections.abc import Callable, Mapping
from time import time_ns
from typing import cast

from bazaar_compute_node.core.models import InboundMessage, OutboundMessage

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
        if not isinstance(channel, TestChannel):
            raise TypeError("test control requires a TestChannel")
        if not isinstance(storage, MemoryStorage):
            raise TypeError("test control requires a MemoryStorage")
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
            payload = request.get("message")
            if not isinstance(payload, Mapping):
                raise ValueError("control inject requires a message object")
            message = self._message_from_control(payload)
            await self._channel.inject(message)
            return {
                "accepted": True,
                "bcn_session_id": message.bcn_session_id,
                "message_id": message.message_id,
            }
        if operation == "status":
            return self._status()
        raise ValueError(f"unsupported test control operation: {operation}")

    def _status(self) -> dict[str, object]:
        return {
            "started": self._is_started(),
            "inbound_messages": {
                session_id: len(messages)
                for session_id, messages in self._storage.inbound_messages.items()
            },
            "runtime_turns": {
                turn_id: {
                    "agent_runtime_session_id": turn.agent_runtime_session_id,
                    "state": turn.state.value,
                }
                for turn_id, turn in self._storage.runtime_turns.items()
            },
            "runtime_sessions": {
                session_id: session.process_state.value
                for session_id, session in self._storage.runtime_sessions.items()
            },
            "cursors": {
                session_id: {
                    "delivered_through_seq": cursor.delivered_through_seq,
                    "inbox_snapshot_seq": cursor.inbox_snapshot_seq,
                }
                for session_id, cursor in self._storage.cursors.items()
            },
            "outbound_messages": [
                _serialize_outbound(message)
                for message in self._storage.outbound_messages.values()
            ],
            "sent_messages": [
                _serialize_outbound(message) for message in self._channel.sent_messages
            ],
            "bcc_commands": [
                {"session_id": session_id, "command": list(command)}
                for session_id, command in self._command_log
            ],
        }

    @staticmethod
    def _message_from_control(payload: Mapping[str, object]) -> InboundMessage:
        bcn_session_id = payload.get("bcn_session_id")
        if not isinstance(bcn_session_id, str) or not bcn_session_id:
            raise ValueError("bcn_session_id must be a non-empty string")

        seq = payload.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise ValueError("seq must be a positive integer")
        body = payload.get("body")
        if not isinstance(body, str):
            raise TypeError("body must be text")
        channel_session_id = payload.get(
            "channel_session_id", f"channel-{bcn_session_id}"
        )
        message_id = payload.get("message_id", f"test-message-{bcn_session_id}-{seq}")
        provider_message_id = payload.get(
            "provider_message_id", f"test-provider-{bcn_session_id}-{seq}"
        )
        canonical_target = payload.get("canonical_target", f"#test:{bcn_session_id}")
        provider_thread_id = payload.get("provider_thread_id", "")
        received_at_ms = payload.get("received_at_ms", time_ns() // 1_000_000)
        provider_time_ms = payload.get("provider_time_ms", received_at_ms)
        sender_id = payload.get("sender_id", "test-sender")
        sender_display_name = payload.get("sender_display_name", "Test")
        message_type = payload.get("message_type", "text")
        channel_slug = payload.get("channel_slug", "test")
        for value, field_name in (
            (channel_session_id, "channel_session_id"),
            (message_id, "message_id"),
            (provider_message_id, "provider_message_id"),
            (canonical_target, "canonical_target"),
            (sender_id, "sender_id"),
            (sender_display_name, "sender_display_name"),
            (message_type, "message_type"),
            (channel_slug, "channel_slug"),
            (provider_thread_id, "provider_thread_id"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        for value, field_name in (
            (received_at_ms, "received_at_ms"),
            (provider_time_ms, "provider_time_ms"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        reply_to_provider_message_id = payload.get("reply_to_provider_message_id")
        if reply_to_provider_message_id is not None and not isinstance(
            reply_to_provider_message_id, str
        ):
            raise TypeError("reply_to_provider_message_id must be a string")
        return InboundMessage(
            seq=seq,
            message_id=cast(str, message_id),
            bcn_session_id=bcn_session_id,
            channel_session_id=cast(str, channel_session_id),
            channel_slug=cast(str, channel_slug),
            provider_message_id=cast(str, provider_message_id),
            received_at_ms=cast(int, received_at_ms),
            sender_id=cast(str, sender_id),
            sender_display_name=cast(str, sender_display_name),
            message_type=cast(str, message_type),
            canonical_target=cast(str, canonical_target),
            body=body,
            provider_time_ms=cast(int, provider_time_ms),
            provider_thread_id=cast(str, provider_thread_id),
            reply_to_provider_message_id=reply_to_provider_message_id,
        )


def _serialize_outbound(message: OutboundMessage) -> dict[str, object]:
    return {
        "outbound_message_id": message.outbound_message_id,
        "command_id": message.command_id,
        "bcn_session_id": message.bcn_session_id,
        "channel_session_id": message.channel_session_id,
        "target": message.target,
        "body": message.body,
        "state": message.state.value,
        "fresh_check_state": message.fresh_check_state.value,
        "created_at_ms": message.created_at_ms,
        "snapshot_seq": message.snapshot_seq,
        "current_inbound_seq": message.current_inbound_seq,
        "provider_message_id": message.provider_message_id,
        "provider_receipt_ref": message.provider_receipt_ref,
        "provider_attempted_at_ms": message.provider_attempted_at_ms,
        "completed_at_ms": message.completed_at_ms,
        "draft_saved_at_ms": message.draft_saved_at_ms,
        "error_kind": message.error_kind,
        "error_message": message.error_message,
        "next_action": message.next_action,
    }


__all__ = ["TestControl"]
