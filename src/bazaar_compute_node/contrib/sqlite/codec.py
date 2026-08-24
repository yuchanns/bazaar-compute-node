from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import aiosqlite

from ...core.models import (
    BcnSession,
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    InboundAttachment,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
    RuntimeAttempt,
    SenderIdentity,
)


def channel_session_from_row(row: aiosqlite.Row) -> ChannelSession:
    following = row["following"]
    if (
        isinstance(following, bool)
        or not isinstance(following, int)
        or following not in (0, 1)
    ):
        raise ValueError("channel session following value is invalid")
    return ChannelSession(
        id=_required_text(row["id"], "id"),
        channel=_required_text(row["channel"], "channel"),
        provider_thread_id=_required_text(
            row["provider_thread_id"], "provider_thread_id"
        ),
        created_at_ms=cast(int, row["created_at_ms"]),
        updated_at_ms=cast(int, row["updated_at_ms"]),
        target_kind=ChannelTargetKind(
            _required_text(row["target_kind"], "channel_session.target_kind")
        ),
        following=bool(following),
        last_inbound_at_ms=cast(int | None, row["last_inbound_at_ms"]),
        last_outbound_at_ms=cast(int | None, row["last_outbound_at_ms"]),
        metadata=_decode_metadata(
            row["provider_identity_ref_json"], "provider_identity_ref_json"
        ),
    )


def bcn_session_from_row(row: aiosqlite.Row) -> BcnSession:
    return BcnSession(
        id=_required_text(row["id"], "id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        workspace_id=_required_text(row["workspace_id"], "workspace_id"),
        created_at_ms=cast(int, row["created_at_ms"]),
        updated_at_ms=cast(int, row["updated_at_ms"]),
        last_activity_at_ms=cast(int | None, row["last_activity_at_ms"]),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def runtime_attempt_from_row(row: aiosqlite.Row) -> RuntimeAttempt:
    return RuntimeAttempt(
        turn_id=_required_text(row["turn_id"], "turn_id"),
        session_id=_required_text(row["session_id"], "session_id"),
        client_user_message_id=_required_text(
            row["client_user_message_id"], "client_user_message_id"
        ),
        started_at_ms=cast(int, row["started_at_ms"]),
    )


def inbound_message_from_row(
    row: aiosqlite.Row,
    attachments: tuple[InboundAttachment, ...] = (),
) -> Message[InboundAttachment]:
    return Message(
        direction=MessageDirection.INBOUND,
        seq=cast(int, row["seq"]),
        message_id=_required_text(row["message_id"], "message_id"),
        session_id=_required_text(row["session_id"], "session_id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        channel=_required_text(row["channel"], "channel"),
        provider_thread_id=_required_text(
            row["provider_thread_id"], "provider_thread_id"
        ),
        provider_message_id=_required_text(
            row["provider_message_id"], "provider_message_id"
        ),
        received_at_ms=cast(int, row["received_at_ms"]),
        sender=(
            SenderIdentity(name=sender)
            if (sender := _optional_text(row["sender"], "sender")) is not None
            else None
        ),
        message_type=_required_text(row["message_type"], "message_type"),
        target=_required_text(row["canonical_target"], "canonical_target"),
        body=_string_value(row["body"], "body", allow_empty=True),
        target_kind=ChannelTargetKind(
            _required_text(row["target_kind"], "inbound_message.target_kind")
        ),
        mentions_agent=bool(_required_boolean(row["mentions_agent"], "mentions_agent")),
        notifies_runtime=bool(
            _required_boolean(row["notifies_runtime"], "notifies_runtime")
        ),
        attachments=attachments,
        provider_time_ms=cast(int | None, row["provider_time_ms"]),
        reply_to_message_id=_optional_string_value(
            row["reply_to_message_id"],
            "reply_to_message_id",
            allow_empty=False,
        ),
        provider_payload_ref=_optional_string_value(
            row["provider_payload_ref"], "provider_payload_ref", allow_empty=False
        ),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def inbound_attachment_from_row(row: aiosqlite.Row) -> InboundAttachment:
    return InboundAttachment(
        attachment_id=_required_text(row["attachment_id"], "attachment_id"),
        name=_required_text(row["name"], "attachment.name"),
        kind=_required_text(row["kind"], "attachment.kind"),
        state=_required_text(row["state"], "attachment.state"),
        media_type=_optional_text(row["media_type"], "attachment.media_type"),
        relative_path=_optional_text(row["relative_path"], "attachment.relative_path"),
        size_bytes=cast(int | None, row["size_bytes"]),
        error=_optional_text(row["error"], "attachment.error"),
    )


def outbound_message_from_row(row: aiosqlite.Row) -> Message[OutboundAttachment]:
    attachments = _decode_outbound_attachments(row["attachments_json"])
    return Message(
        direction=MessageDirection.OUTBOUND,
        seq=0,
        message_id=_required_text(row["outbound_message_id"], "outbound_message_id"),
        command_id=_required_text(row["command_id"], "command_id"),
        session_id=_required_text(row["session_id"], "session_id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        target=_required_text(row["target"], "target"),
        body=_string_value(row["body"], "body", allow_empty=True),
        attachments=attachments,
        reply_to_message_id=_optional_text(
            row["reply_to_message_id"],
            "reply_to_message_id",
        ),
        delivery_state=OutboundDeliveryState(
            _required_text(row["state"], "outbound_message.state")
        ),
        created_at_ms=cast(int, row["created_at_ms"]),
        snapshot_seq=cast(int, row["snapshot_seq"]),
        current_inbound_seq=cast(int, row["current_inbound_seq"]),
        provider_message_id=_optional_text(
            row["provider_message_id"], "provider_message_id"
        ),
        provider_receipt_ref=_optional_text(
            row["provider_receipt_ref"], "provider_receipt_ref"
        ),
        provider_attempted_at_ms=cast(int, row["provider_attempted_at_ms"]),
        completed_at_ms=cast(int | None, row["completed_at_ms"]),
        error_kind=_optional_text(row["error_kind"], "error_kind"),
        error_message=_optional_text(row["error_message"], "error_message"),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def message_from_row(
    row: aiosqlite.Row,
    inbound_attachments: tuple[InboundAttachment, ...] = (),
) -> Message[InboundAttachment | OutboundAttachment]:
    direction = MessageDirection(_required_text(row["direction"], "direction"))
    sender = _optional_text(row["sender"], "sender")
    common = {
        "direction": direction,
        "seq": cast(int, row["seq"]),
        "message_id": _required_text(row["message_id"], "message_id"),
        "session_id": _required_text(row["session_id"], "session_id"),
        "channel_session_id": _required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        "channel": _required_text(row["channel"], "channel"),
        "provider_thread_id": _required_text(
            row["provider_thread_id"], "provider_thread_id"
        ),
        "provider_message_id": _optional_text(
            row["provider_message_id"], "provider_message_id"
        ),
        "sender": SenderIdentity(name=sender) if sender is not None else None,
        "message_type": _required_text(row["message_type"], "message_type"),
        "target": _required_text(row["target"], "target"),
        "target_kind": ChannelTargetKind(
            _required_text(row["target_kind"], "message.target_kind")
        ),
        "reply_to_message_id": _optional_text(
            row["reply_to_message_id"], "reply_to_message_id"
        ),
        "body": _string_value(row["body"], "body", allow_empty=True),
        "metadata": _decode_metadata(row["metadata_json"], "metadata_json"),
    }
    if direction is MessageDirection.INBOUND:
        return Message(
            **common,
            attachments=inbound_attachments,
            provider_time_ms=cast(int | None, row["provider_time_ms"]),
            received_at_ms=cast(int, row["received_at_ms"]),
            mentions_agent=bool(
                _required_boolean(row["mentions_agent"], "mentions_agent")
            ),
            notifies_runtime=bool(
                _required_boolean(row["notifies_runtime"], "notifies_runtime")
            ),
            provider_payload_ref=_optional_text(
                row["provider_payload_ref"], "provider_payload_ref"
            ),
        )
    return Message(
        **common,
        attachments=_decode_outbound_attachments(row["attachments_json"]),
        command_id=_required_text(row["command_id"], "command_id"),
        delivery_state=OutboundDeliveryState(
            _required_text(row["delivery_state"], "message.delivery_state")
        ),
        snapshot_seq=cast(int, row["snapshot_seq"]),
        current_inbound_seq=cast(int, row["current_inbound_seq"]),
        created_at_ms=cast(int, row["created_at_ms"]),
        provider_attempted_at_ms=cast(int, row["provider_attempted_at_ms"]),
        provider_receipt_ref=_optional_text(
            row["provider_receipt_ref"], "provider_receipt_ref"
        ),
        completed_at_ms=cast(int | None, row["completed_at_ms"]),
        error_kind=_optional_text(row["error_kind"], "error_kind"),
        error_message=_optional_text(row["error_message"], "error_message"),
    )


def _decode_outbound_attachments(raw_value: object) -> tuple[OutboundAttachment, ...]:
    raw_attachments = raw_value
    if not isinstance(raw_attachments, str):
        raise TypeError("attachments_json must be text")
    try:
        attachment_values = json.loads(raw_attachments)
    except json.JSONDecodeError as error:
        raise ValueError("attachments_json must contain valid JSON") from error
    if not isinstance(attachment_values, list):
        raise TypeError("attachments_json must contain a list")
    attachments: list[OutboundAttachment] = []
    for index, value in enumerate(attachment_values):
        if not isinstance(value, dict):
            raise TypeError(f"attachments_json[{index}] must be an object")
        attachments.append(
            OutboundAttachment(
                name=_required_text(value.get("name"), f"attachments[{index}].name"),
                relative_path=_required_text(
                    value.get("relative_path"),
                    f"attachments[{index}].relative_path",
                ),
                media_type=_optional_text(
                    value.get("media_type"), f"attachments[{index}].media_type"
                ),
                size_bytes=cast(int, value.get("size_bytes")),
                sha256=_required_text(
                    value.get("sha256"), f"attachments[{index}].sha256"
                ),
            )
        )
    return tuple(attachments)


def consumer_cursor_from_row(row: aiosqlite.Row) -> ConsumerCursor:
    return ConsumerCursor(
        session_id=_required_text(row["session_id"], "session_id"),
        delivered_through_seq=cast(int, row["delivered_through_seq"]),
        inbox_snapshot_seq=cast(int | None, row["inbox_snapshot_seq"]),
        inbox_snapshot_source=_optional_string_value(
            row["inbox_snapshot_source"],
            "inbox_snapshot_source",
            allow_empty=False,
        ),
        inbox_snapshot_at_ms=cast(int | None, row["inbox_snapshot_at_ms"]),
        last_check_at_ms=cast(int | None, row["last_check_at_ms"]),
        last_read_at_ms=cast(int | None, row["last_read_at_ms"]),
        updated_at_ms=cast(int, row["updated_at_ms"]),
    )


def validate_inbound_message_input(message: object) -> None:
    if not isinstance(message, Message):
        raise TypeError("message must be a Message")
    if message.direction is not MessageDirection.INBOUND:
        raise ValueError("inbound persistence requires an inbound message")


def validate_message_input(message: object) -> None:
    if not isinstance(message, Message):
        raise TypeError("message must be a Message")
    if message.direction is MessageDirection.INBOUND:
        validate_inbound_message_input(message)
    else:
        validate_outbound_message_input(message)


def validate_outbound_message_input(message: object) -> None:
    if not isinstance(message, Message):
        raise TypeError("message must be a Message")
    if message.direction is not MessageDirection.OUTBOUND:
        raise ValueError("outbound persistence requires an outbound message")
    if not isinstance(message.delivery_state, OutboundDeliveryState):
        raise TypeError("outbound message state is invalid")
    delivery_state = message.delivery_state
    if not isinstance(message.body, str):
        raise TypeError("outbound body must be a string")
    for value, field_name in (
        (message.reply_to_message_id, "reply_to_message_id"),
        (message.provider_message_id, "provider_message_id"),
        (message.provider_receipt_ref, "provider_receipt_ref"),
        (message.error_kind, "error_kind"),
        (message.error_message, "error_message"),
    ):
        _validate_optional_input_text(value, field_name)
    snapshot_seq = message.snapshot_seq
    current_inbound_seq = message.current_inbound_seq
    created_at_ms = message.created_at_ms
    provider_attempted_at_ms = message.provider_attempted_at_ms
    assert snapshot_seq is not None
    assert current_inbound_seq is not None
    assert created_at_ms is not None
    assert provider_attempted_at_ms is not None
    if current_inbound_seq > snapshot_seq:
        raise ValueError("outbound current inbound sequence exceeds snapshot sequence")
    if provider_attempted_at_ms < created_at_ms:
        raise ValueError("outbound provider attempt cannot precede creation")
    if (
        message.completed_at_ms is not None
        and message.completed_at_ms < provider_attempted_at_ms
    ):
        raise ValueError("outbound completion cannot precede provider attempt")
    if (
        delivery_state
        in {
            OutboundDeliveryState.PENDING,
            OutboundDeliveryState.QUEUED,
        }
        and message.completed_at_ms is not None
    ):
        raise ValueError("non-terminal outbound message cannot be terminal")
    if (
        delivery_state
        in {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
        }
        and message.completed_at_ms is None
    ):
        raise ValueError("terminal outbound message requires completed_at_ms")
    if (
        delivery_state in {OutboundDeliveryState.SENT, OutboundDeliveryState.PARTIAL}
        and message.provider_message_id is None
        and message.provider_receipt_ref is None
    ):
        raise ValueError("delivered outbound message requires a provider receipt")


def validate_outbound_insert(message: Message[OutboundAttachment]) -> None:
    if message.delivery_state is not OutboundDeliveryState.PENDING:
        raise ValueError("a new outbound message must start in pending state")
    if any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.completed_at_ms,
            message.error_kind,
            message.error_message,
        )
    ):
        raise ValueError(
            "a new pending outbound message cannot contain result evidence"
        )


def validate_outbound_update(
    existing: Message[OutboundAttachment],
    incoming: Message[OutboundAttachment],
) -> Message[OutboundAttachment]:
    if (
        incoming.snapshot_seq != existing.snapshot_seq
        or incoming.current_inbound_seq != existing.current_inbound_seq
    ):
        raise ValueError("outbound snapshot evidence cannot change")

    incoming_state = incoming.delivery_state
    existing_state = existing.delivery_state
    if incoming_state is None or existing_state is None:
        raise RuntimeError("outbound message has no delivery state")
    if existing_state is incoming_state:
        transitioned = existing
    else:
        transitioned = existing.transition_to(
            incoming_state,
            at_ms=_outbound_transition_time(incoming),
            provider_message_id=incoming.provider_message_id,
            provider_receipt_ref=incoming.provider_receipt_ref,
            error_kind=incoming.error_kind,
            error_message=incoming.error_message,
        )

    if (
        transitioned.completed_at_ms is not None
        and incoming.completed_at_ms is not None
        and transitioned.completed_at_ms != incoming.completed_at_ms
    ):
        raise ValueError("outbound completion time cannot change")
    return replace(
        transitioned,
        provider_message_id=_merge_optional_text(
            transitioned.provider_message_id,
            incoming.provider_message_id,
            "provider_message_id",
        ),
        provider_receipt_ref=_merge_optional_text(
            transitioned.provider_receipt_ref,
            incoming.provider_receipt_ref,
            "provider_receipt_ref",
        ),
        provider_attempted_at_ms=_merge_timestamp(
            transitioned.provider_attempted_at_ms,
            incoming.provider_attempted_at_ms,
            "provider_attempted_at_ms",
        ),
        completed_at_ms=transitioned.completed_at_ms
        if transitioned.completed_at_ms is not None
        else incoming.completed_at_ms,
        error_kind=incoming.error_kind or transitioned.error_kind,
        error_message=incoming.error_message or transitioned.error_message,
        metadata=incoming.metadata,
    )


def _outbound_transition_time(message: Message[OutboundAttachment]) -> int:
    transition_time = message.completed_at_ms or message.provider_attempted_at_ms
    if transition_time is None:
        raise RuntimeError("outbound message has no transition time")
    return transition_time


def _merge_optional_text(
    existing: str | None,
    incoming: str | None,
    field_name: str,
) -> str | None:
    if existing is not None and incoming is not None and existing != incoming:
        raise ValueError(f"outbound {field_name} cannot change")
    return incoming or existing


def _merge_timestamp(
    existing: int | None,
    incoming: int | None,
    field_name: str,
) -> int | None:
    if existing is not None and incoming is not None and existing != incoming:
        raise ValueError(f"outbound {field_name} cannot change")
    return incoming if incoming is not None else existing


def _validate_optional_input_text(value: object, field_name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{field_name} must be a non-empty string when present")


def validate_consumer_cursor_input(cursor: object) -> None:
    if not isinstance(cursor, ConsumerCursor):
        raise TypeError("cursor must be a ConsumerCursor")
    source = cursor.inbox_snapshot_source
    if source is not None and source not in {"check", "read"}:
        raise ValueError("inbox_snapshot_source must be 'check' or 'read'")
    if cursor.inbox_snapshot_seq is None:
        if cursor.inbox_snapshot_source is not None:
            raise ValueError("inbox snapshot source requires a snapshot sequence")
        if cursor.inbox_snapshot_at_ms is not None:
            raise ValueError("inbox snapshot time requires a snapshot sequence")
    elif cursor.inbox_snapshot_at_ms is None:
        raise ValueError("inbox snapshot sequence requires a snapshot time")


def validate_cursor_bounds(
    cursor: ConsumerCursor,
    *,
    latest_inbound_seq: int,
    latest_message_seq: int,
) -> None:
    if cursor.delivered_through_seq > latest_inbound_seq:
        raise ValueError("delivered cursor cannot exceed the latest inbound sequence")
    if (
        cursor.inbox_snapshot_seq is not None
        and cursor.inbox_snapshot_seq > latest_message_seq
    ):
        raise ValueError("inbox snapshot cannot exceed the latest message sequence")


def validate_consumer_cursor_update(
    existing: ConsumerCursor,
    incoming: ConsumerCursor,
) -> None:
    if incoming.updated_at_ms < existing.updated_at_ms:
        raise ValueError("consumer cursor updated_at_ms cannot move backwards")
    if incoming.delivered_through_seq < existing.delivered_through_seq:
        raise ValueError("delivered cursor cannot move backwards")
    if existing.inbox_snapshot_seq is not None and (
        incoming.inbox_snapshot_seq is None
        or incoming.inbox_snapshot_seq < existing.inbox_snapshot_seq
    ):
        raise ValueError("inbox snapshot cannot move backwards")
    if (
        incoming.inbox_snapshot_source == "read"
        and incoming.delivered_through_seq != existing.delivered_through_seq
    ):
        raise ValueError("read snapshot cannot advance the delivered cursor")
    for incoming_value, existing_value, field_name in (
        (
            incoming.inbox_snapshot_at_ms,
            existing.inbox_snapshot_at_ms,
            "inbox_snapshot_at_ms",
        ),
        (incoming.last_check_at_ms, existing.last_check_at_ms, "last_check_at_ms"),
        (incoming.last_read_at_ms, existing.last_read_at_ms, "last_read_at_ms"),
    ):
        if (
            incoming_value is not None
            and existing_value is not None
            and incoming_value < existing_value
        ):
            raise ValueError(f"{field_name} cannot move backwards")


def validate_channel_session_input(session: ChannelSession) -> None:
    if not isinstance(session.following, bool):
        raise TypeError("channel session following must be a boolean")


def validate_channel_session_update(
    existing: ChannelSession,
    incoming: ChannelSession,
) -> ChannelSession:
    if (
        existing.channel != incoming.channel
        or existing.provider_thread_id != incoming.provider_thread_id
        or existing.target_kind is not incoming.target_kind
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("channel session identity cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    return replace(
        existing,
        updated_at_ms=incoming.updated_at_ms,
        following=incoming.following,
        last_inbound_at_ms=incoming.last_inbound_at_ms,
        last_outbound_at_ms=incoming.last_outbound_at_ms,
        metadata=incoming.metadata,
    )


def validate_bcn_session_update(
    existing: BcnSession,
    incoming: BcnSession,
) -> BcnSession:
    if (
        existing.channel_session_id != incoming.channel_session_id
        or existing.workspace_id != incoming.workspace_id
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("bcn session binding cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    return replace(
        existing,
        updated_at_ms=incoming.updated_at_ms,
        last_activity_at_ms=incoming.last_activity_at_ms,
        metadata=incoming.metadata,
    )


def _validate_updated_at(existing: int, incoming: int) -> None:
    if incoming < existing:
        raise ValueError("session updated_at_ms cannot move backwards")


def _required_text(value: object, field_name: str) -> str:
    return _string_value(value, field_name, allow_empty=False)


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string_value(value, field_name, allow_empty=False)


def _optional_string_value(
    value: object,
    field_name: str,
    *,
    allow_empty: bool,
) -> str | None:
    if value is None:
        return None
    return _string_value(value, field_name, allow_empty=allow_empty)


def _string_value(value: object, field_name: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        requirement = "a string" if allow_empty else "a non-empty string"
        raise ValueError(f"{field_name} must be {requirement}")
    return value


def _required_boolean(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
        raise ValueError(f"{field_name} must be a boolean integer")
    return value


def encode_metadata(metadata: object) -> str:
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping")
    if any(not isinstance(key, str) for key in metadata):
        raise ValueError("metadata keys must be strings")
    try:
        return json.dumps(
            dict(metadata),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("metadata must be JSON serializable") from error


def _decode_metadata(value: object, field_name: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must contain a JSON object")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field_name} contains invalid JSON") from error
    if not isinstance(decoded, dict):
        raise TypeError(f"{field_name} must contain a JSON object")
    return decoded
