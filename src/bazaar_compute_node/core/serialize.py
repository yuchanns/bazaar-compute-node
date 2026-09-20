"""Messages and conversations as they are handed across a process boundary."""

from __future__ import annotations

from .command import TargetProjection
from .models import (
    InboundAttachment,
    InboxTargetSummary,
    Message,
    MessageDirection,
    OutboundAttachment,
)


def serialize_attachment(
    attachment: InboundAttachment | OutboundAttachment,
) -> dict[str, object]:
    if isinstance(attachment, InboundAttachment):
        return {
            "attachment_id": attachment.attachment_id,
            "name": attachment.name,
            "kind": attachment.kind,
            "state": attachment.state,
            "media_type": attachment.media_type,
            "relative_path": attachment.relative_path,
            "size_bytes": attachment.size_bytes,
            "error": attachment.error,
            "sha256": None,
        }
    return {
        "attachment_id": None,
        "name": attachment.name,
        "kind": "file",
        "state": "ready",
        "media_type": attachment.media_type,
        "relative_path": attachment.relative_path,
        "size_bytes": attachment.size_bytes,
        "error": None,
        "sha256": attachment.sha256,
    }


def serialize_message(
    message: Message,
    target_projections: tuple[TargetProjection, ...] = (),
) -> dict[str, object]:
    targets = {
        projection.canonical_target: projection.display_target
        for projection in target_projections
    }
    return {
        "seq": message.seq,
        "message_id": message.message_id,
        "direction": message.direction.value,
        "thread_id": message.thread_id,
        "channel_session_id": message.channel_session_id,
        "channel": message.channel,
        "received_at_ms": message.received_at_ms,
        "created_at_ms": message.created_at_ms,
        "provider_time_ms": message.provider_time_ms,
        "sender": (
            None
            if message.sender is None
            else {
                "id": message.sender.id,
                "name": message.sender.name,
                "display_name": message.sender.display_name,
            }
        ),
        "sender_kind": message.sender_kind.value,
        "system_message_kind": (
            None
            if message.system_message_kind is None
            else message.system_message_kind.value
        ),
        "system_message_source_target": (
            targets.get(source_target, source_target)
            if isinstance(
                source_target := message.metadata.get("system_message_source_target"),
                str,
            )
            else None
        ),
        "system_message_source_message_id": message.metadata.get(
            "system_message_source_message_id"
        ),
        "message_type": message.message_type,
        "target": targets.get(message.target, message.target),
        "canonical_target": message.target,
        "target_kind": message.target_kind.value,
        "mentions_agent": message.mentions_agent,
        "notifies_runtime": message.notifies_runtime,
        "attachments": [
            serialize_attachment(attachment) for attachment in message.attachments
        ],
        "body": message.body,
        "reply_to_message_id": message.reply_to_message_id,
        "delivery_state": (
            message.delivery_state.value
            if message.direction is MessageDirection.OUTBOUND
            and message.delivery_state is not None
            else None
        ),
    }


def serialize_inbox_target(summary: InboxTargetSummary) -> dict[str, object]:
    sender = summary.latest_sender
    latest_time_ms = (
        summary.latest_provider_time_ms
        if summary.latest_provider_time_ms is not None
        else summary.latest_received_at_ms
    )
    return {
        "target": summary.target,
        "canonical_target": summary.canonical_target or summary.target,
        "thread_id": summary.thread_id,
        "review": summary.review.value,
        "target_kind": summary.target_kind.value,
        "channel": summary.channel,
        "pending_count": summary.pending_count,
        "last_activity_at_ms": summary.last_activity_at_ms,
        "latest_message_id": summary.latest_message_id,
        "latest_sender": (
            None
            if sender is None
            else {
                "id": sender.id,
                "name": sender.name,
                "display_name": sender.display_name,
            }
        ),
        "latest_time_ms": latest_time_ms,
    }


__all__ = ["serialize_attachment", "serialize_inbox_target", "serialize_message"]
