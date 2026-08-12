from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace

import aiosqlite

from ...core.models import (
    BcnSession,
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    FreshCheckState,
    InboundAttachment,
    InboundMessage,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeAttempt,
    RuntimeSession,
)


def _channel_session_from_row(row: aiosqlite.Row) -> ChannelSession:
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
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        updated_at_ms=_required_non_negative_int(row["updated_at_ms"], "updated_at_ms"),
        target_kind=ChannelTargetKind(
            _required_text(row["target_kind"], "channel_session.target_kind")
        ),
        following=bool(following),
        last_inbound_at_ms=_optional_non_negative_int(
            row["last_inbound_at_ms"], "last_inbound_at_ms"
        ),
        last_outbound_at_ms=_optional_non_negative_int(
            row["last_outbound_at_ms"], "last_outbound_at_ms"
        ),
        metadata=_decode_metadata(
            row["provider_identity_ref_json"], "provider_identity_ref_json"
        ),
    )


def _bcn_session_from_row(row: aiosqlite.Row) -> BcnSession:
    return BcnSession(
        id=_required_text(row["id"], "id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        workspace_id=_required_text(row["workspace_id"], "workspace_id"),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        updated_at_ms=_required_non_negative_int(row["updated_at_ms"], "updated_at_ms"),
        last_activity_at_ms=_optional_non_negative_int(
            row["last_activity_at_ms"], "last_activity_at_ms"
        ),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def _runtime_session_from_row(row: aiosqlite.Row) -> RuntimeSession:
    return RuntimeSession(
        id=_required_text(row["id"], "id"),
        bcn_session_id=_required_text(row["bcn_session_id"], "bcn_session_id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        runtime=_required_text(row["runtime"], "runtime"),
        workspace_id=_required_text(row["workspace_id"], "workspace_id"),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        updated_at_ms=_required_non_negative_int(row["updated_at_ms"], "updated_at_ms"),
        provider_thread_id=_optional_text(
            row["provider_thread_id"], "provider_thread_id"
        ),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def _runtime_attempt_from_row(row: aiosqlite.Row) -> RuntimeAttempt:
    return RuntimeAttempt(
        turn_id=_required_text(row["turn_id"], "turn_id"),
        session_id=_required_text(row["session_id"], "session_id"),
        client_user_message_id=_required_text(
            row["client_user_message_id"], "client_user_message_id"
        ),
        started_at_ms=_required_non_negative_int(row["started_at_ms"], "started_at_ms"),
    )


def _inbound_message_from_row(
    row: aiosqlite.Row,
    attachments: tuple[InboundAttachment, ...] = (),
) -> InboundMessage:
    return InboundMessage(
        seq=_required_non_negative_int(row["seq"], "seq"),
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
        received_at_ms=_required_non_negative_int(
            row["received_at_ms"], "received_at_ms"
        ),
        sender=_optional_text(row["sender"], "sender"),
        message_type=_required_text(row["message_type"], "message_type"),
        canonical_target=_required_text(row["canonical_target"], "canonical_target"),
        body=_string_value(row["body"], "body", allow_empty=True),
        target_kind=ChannelTargetKind(
            _required_text(row["target_kind"], "inbound_message.target_kind")
        ),
        mentions_agent=bool(_required_boolean(row["mentions_agent"], "mentions_agent")),
        notifies_runtime=bool(
            _required_boolean(row["notifies_runtime"], "notifies_runtime")
        ),
        attachments=attachments,
        provider_time_ms=_optional_non_negative_int(
            row["provider_time_ms"], "provider_time_ms"
        ),
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


def _inbound_attachment_from_row(row: aiosqlite.Row) -> InboundAttachment:
    return InboundAttachment(
        attachment_id=_required_text(row["attachment_id"], "attachment_id"),
        name=_required_text(row["name"], "attachment.name"),
        kind=_required_text(row["kind"], "attachment.kind"),
        state=_required_text(row["state"], "attachment.state"),
        media_type=_optional_text(row["media_type"], "attachment.media_type"),
        relative_path=_optional_text(row["relative_path"], "attachment.relative_path"),
        size_bytes=_optional_non_negative_int(
            row["size_bytes"], "attachment.size_bytes"
        ),
        error=_optional_text(row["error"], "attachment.error"),
    )


def _outbound_message_from_row(row: aiosqlite.Row) -> OutboundMessage:
    return OutboundMessage(
        outbound_message_id=_required_text(
            row["outbound_message_id"], "outbound_message_id"
        ),
        command_id=_required_text(row["command_id"], "command_id"),
        session_id=_required_text(row["session_id"], "session_id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        target=_required_text(row["target"], "target"),
        body=_string_value(row["body"], "body", allow_empty=True),
        reply_to_message_id=_optional_text(
            row["reply_to_message_id"],
            "reply_to_message_id",
        ),
        state=OutboundDeliveryState(
            _required_text(row["state"], "outbound_message.state")
        ),
        fresh_check_state=FreshCheckState(
            _required_text(
                row["fresh_check_state"], "outbound_message.fresh_check_state"
            )
        ),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        snapshot_seq=_optional_non_negative_int(row["snapshot_seq"], "snapshot_seq"),
        current_inbound_seq=_optional_non_negative_int(
            row["current_inbound_seq"], "current_inbound_seq"
        ),
        provider_message_id=_optional_text(
            row["provider_message_id"], "provider_message_id"
        ),
        provider_receipt_ref=_optional_text(
            row["provider_receipt_ref"], "provider_receipt_ref"
        ),
        provider_attempted_at_ms=_optional_non_negative_int(
            row["provider_attempted_at_ms"], "provider_attempted_at_ms"
        ),
        completed_at_ms=_optional_non_negative_int(
            row["completed_at_ms"], "completed_at_ms"
        ),
        draft_saved_at_ms=_optional_non_negative_int(
            row["draft_saved_at_ms"], "draft_saved_at_ms"
        ),
        error_kind=_optional_text(row["error_kind"], "error_kind"),
        error_message=_optional_text(row["error_message"], "error_message"),
        next_action=_optional_text(row["next_action"], "next_action"),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def _consumer_cursor_from_row(row: aiosqlite.Row) -> ConsumerCursor:
    return ConsumerCursor(
        session_id=_required_text(row["session_id"], "session_id"),
        delivered_through_seq=_required_non_negative_int(
            row["delivered_through_seq"], "delivered_through_seq"
        ),
        inbox_snapshot_seq=_optional_non_negative_int(
            row["inbox_snapshot_seq"], "inbox_snapshot_seq"
        ),
        inbox_snapshot_source=_optional_string_value(
            row["inbox_snapshot_source"],
            "inbox_snapshot_source",
            allow_empty=False,
        ),
        inbox_snapshot_at_ms=_optional_non_negative_int(
            row["inbox_snapshot_at_ms"], "inbox_snapshot_at_ms"
        ),
        last_check_at_ms=_optional_non_negative_int(
            row["last_check_at_ms"], "last_check_at_ms"
        ),
        last_read_at_ms=_optional_non_negative_int(
            row["last_read_at_ms"], "last_read_at_ms"
        ),
        updated_at_ms=_required_non_negative_int(row["updated_at_ms"], "updated_at_ms"),
    )


def _validate_inbound_message_input(message: InboundMessage) -> None:
    if not isinstance(message, InboundMessage):
        raise TypeError("message must be an InboundMessage")


def _validate_outbound_message_input(message: OutboundMessage) -> None:
    if not isinstance(message, OutboundMessage):
        raise TypeError("message must be an OutboundMessage")
    if not isinstance(message.state, OutboundDeliveryState):
        raise TypeError("outbound message state is invalid")
    if not isinstance(message.fresh_check_state, FreshCheckState):
        raise TypeError("outbound fresh-check state is invalid")
    if not isinstance(message.body, str):
        raise TypeError("outbound body must be a string")
    for value, field_name in (
        (message.reply_to_message_id, "reply_to_message_id"),
        (message.provider_message_id, "provider_message_id"),
        (message.provider_receipt_ref, "provider_receipt_ref"),
        (message.error_kind, "error_kind"),
        (message.error_message, "error_message"),
        (message.next_action, "next_action"),
    ):
        _validate_optional_input_text(value, field_name)
    if message.fresh_check_state is FreshCheckState.REQUIRED and (
        message.snapshot_seq is not None or message.current_inbound_seq is not None
    ):
        raise ValueError("a required outbound fresh check cannot contain evidence")
    if message.fresh_check_state is FreshCheckState.PASSED:
        if message.snapshot_seq is None or message.current_inbound_seq is None:
            raise ValueError("a passed outbound fresh check requires sequence bounds")
        if message.current_inbound_seq > message.snapshot_seq:
            raise ValueError(
                "outbound current inbound sequence exceeds snapshot sequence"
            )
    if (
        message.state
        in {
            OutboundDeliveryState.PENDING,
            OutboundDeliveryState.QUEUED,
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
        }
        and message.fresh_check_state is not FreshCheckState.PASSED
    ):
        raise ValueError("outbound delivery state requires a passed fresh check")
    if (
        message.state is OutboundDeliveryState.REJECTED
        and message.fresh_check_state is FreshCheckState.PASSED
    ):
        raise ValueError("rejected outbound message cannot have a passed fresh check")
    for value, field_name in (
        (message.provider_attempted_at_ms, "provider_attempted_at_ms"),
        (message.completed_at_ms, "completed_at_ms"),
        (message.draft_saved_at_ms, "draft_saved_at_ms"),
    ):
        if value is not None and value < message.created_at_ms:
            raise ValueError(f"outbound {field_name} cannot precede creation")
    if message.state is OutboundDeliveryState.DRAFT and any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.provider_attempted_at_ms,
            message.completed_at_ms,
            message.draft_saved_at_ms,
        )
    ):
        raise ValueError("draft outbound message cannot contain delivery evidence")
    if message.state in {
        OutboundDeliveryState.PENDING,
        OutboundDeliveryState.QUEUED,
    } and (
        message.completed_at_ms is not None or message.draft_saved_at_ms is not None
    ):
        raise ValueError("non-terminal outbound message cannot be terminal")
    if message.state is OutboundDeliveryState.REJECTED and any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.provider_attempted_at_ms,
        )
    ):
        raise ValueError("rejected outbound message cannot contain provider evidence")
    if (
        message.state
        in {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
            OutboundDeliveryState.REJECTED,
        }
        and message.completed_at_ms is None
    ):
        raise ValueError("terminal outbound message requires completed_at_ms")
    if (
        message.state is OutboundDeliveryState.REJECTED
        and message.draft_saved_at_ms is None
    ):
        raise ValueError("rejected outbound message requires draft_saved_at_ms")
    if (
        message.state in {OutboundDeliveryState.SENT, OutboundDeliveryState.PARTIAL}
        and message.provider_message_id is None
        and message.provider_receipt_ref is None
    ):
        raise ValueError("delivered outbound message requires a provider receipt")


def _validate_outbound_insert(message: OutboundMessage) -> None:
    if message.state is not OutboundDeliveryState.DRAFT:
        raise ValueError("a new outbound message must start in draft state")
    if message.fresh_check_state is not FreshCheckState.REQUIRED:
        raise ValueError("a new outbound draft requires a required fresh check")
    if any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.provider_attempted_at_ms,
            message.completed_at_ms,
            message.draft_saved_at_ms,
        )
    ):
        raise ValueError("a new outbound draft cannot contain delivery timestamps")


def _validate_outbound_update(
    existing: OutboundMessage,
    incoming: OutboundMessage,
) -> OutboundMessage:
    candidate = existing
    sequence_changed = (
        incoming.snapshot_seq != existing.snapshot_seq
        or incoming.current_inbound_seq != existing.current_inbound_seq
    )
    fresh_state_changed = incoming.fresh_check_state is not existing.fresh_check_state
    if existing.fresh_check_state is FreshCheckState.REQUIRED:
        if fresh_state_changed or sequence_changed:
            candidate = existing.record_fresh_check(
                incoming.fresh_check_state,
                snapshot_seq=incoming.snapshot_seq,
                current_inbound_seq=incoming.current_inbound_seq,
            )
    elif fresh_state_changed or sequence_changed:
        raise ValueError("outbound fresh-check evidence cannot change")

    if candidate.state is incoming.state:
        transitioned = candidate
    else:
        transitioned = candidate.transition_to(
            incoming.state,
            at_ms=_outbound_transition_time(incoming),
            provider_message_id=incoming.provider_message_id,
            provider_receipt_ref=incoming.provider_receipt_ref,
            error_kind=incoming.error_kind,
            error_message=incoming.error_message,
            next_action=incoming.next_action,
        )

    if (
        transitioned.completed_at_ms is not None
        and incoming.completed_at_ms is not None
        and transitioned.completed_at_ms != incoming.completed_at_ms
    ):
        raise ValueError("outbound completion time cannot change")
    if (
        transitioned.draft_saved_at_ms is not None
        and incoming.draft_saved_at_ms is not None
        and transitioned.draft_saved_at_ms != incoming.draft_saved_at_ms
    ):
        raise ValueError("outbound draft time cannot change")
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
        draft_saved_at_ms=transitioned.draft_saved_at_ms
        if transitioned.draft_saved_at_ms is not None
        else incoming.draft_saved_at_ms,
        error_kind=incoming.error_kind or transitioned.error_kind,
        error_message=incoming.error_message or transitioned.error_message,
        next_action=incoming.next_action or transitioned.next_action,
        metadata=incoming.metadata,
    )


def _outbound_transition_time(message: OutboundMessage) -> int:
    return (
        message.completed_at_ms
        or message.draft_saved_at_ms
        or message.provider_attempted_at_ms
        or message.created_at_ms
    )


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


def _validate_consumer_cursor_input(cursor: ConsumerCursor) -> None:
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


def _validate_cursor_bounds(cursor: ConsumerCursor, latest_seq: int) -> None:
    if cursor.delivered_through_seq > latest_seq:
        raise ValueError("delivered cursor cannot exceed the latest inbound sequence")
    if cursor.inbox_snapshot_seq is not None and cursor.inbox_snapshot_seq > latest_seq:
        raise ValueError("inbox snapshot cannot exceed the latest inbound sequence")


def _validate_consumer_cursor_update(
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


def _validate_channel_session_input(session: ChannelSession) -> None:
    if not isinstance(session.following, bool):
        raise TypeError("channel session following must be a boolean")


def _validate_channel_session_update(
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


def _validate_bcn_session_update(
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


def _validate_runtime_session_update(
    existing: RuntimeSession,
    incoming: RuntimeSession,
) -> RuntimeSession:
    if (
        existing.bcn_session_id != incoming.bcn_session_id
        or existing.channel_session_id != incoming.channel_session_id
        or existing.runtime != incoming.runtime
        or existing.workspace_id != incoming.workspace_id
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("runtime session binding cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    return replace(
        existing,
        updated_at_ms=incoming.updated_at_ms,
        provider_thread_id=incoming.provider_thread_id,
        metadata=incoming.metadata,
    )


def _validate_updated_at(existing: int, incoming: int) -> None:
    if incoming < existing:
        raise ValueError("session updated_at_ms cannot move backwards")


def _required_text(value: object, field_name: str) -> str:
    return _string_value(value, field_name, allow_empty=False)


def _validate_non_empty_text(value: object, field_name: str) -> None:
    _required_text(value, field_name)


def _validate_non_negative_int(value: object, field_name: str) -> None:
    _required_non_negative_int(value, field_name)


def _validate_positive_int(value: object, field_name: str) -> None:
    _required_positive_int(value, field_name)


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


def _required_non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _required_positive_int(value: object, field_name: str) -> int:
    result = _required_non_negative_int(value, field_name)
    if result == 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return result


def _required_boolean(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
        raise ValueError(f"{field_name} must be a boolean integer")
    return value


def _optional_non_negative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _required_non_negative_int(value, field_name)


def _encode_metadata(metadata: Mapping[str, object]) -> str:
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
