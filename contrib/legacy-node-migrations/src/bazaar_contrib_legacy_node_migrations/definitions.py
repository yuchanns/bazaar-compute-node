"""Node table and index definitions shared by upgrades and the current baseline."""

import bazaar_contrib_storage as s

NODE_STATE_V1 = s.Table(
    name="node_state",
    columns=(
        s.ColumnDefinition(
            name="singleton_key", type="integer", nullable=True, generated=True
        ),
        s.ColumnDefinition(name="node_id", type="text", nullable=True),
        s.ColumnDefinition(name="schema_version", type="integer", nullable=True),
        s.ColumnDefinition(name="workspace_id", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="updated_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
    ),
    primary_key=("singleton_key",),
)


CHANNEL_SESSIONS_V1 = s.Table(
    name="channel_sessions",
    columns=(
        s.ColumnDefinition(name="id", type="text", nullable=True),
        s.ColumnDefinition(name="channel", type="text", nullable=True),
        s.ColumnDefinition(name="provider_thread_id", type="text", nullable=True),
        s.ColumnDefinition(name="target_kind", type="text", nullable=True),
        s.ColumnDefinition(name="following", type="integer", nullable=True),
        s.ColumnDefinition(
            name="provider_identity_ref_json", type="text", nullable=True
        ),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="updated_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="last_inbound_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="last_outbound_at_ms", type="integer", nullable=True),
    ),
    primary_key=("id",),
)


BCN_SESSIONS_V1 = s.Table(
    name="bcn_sessions",
    columns=(
        s.ColumnDefinition(name="id", type="text", nullable=True),
        s.ColumnDefinition(name="channel_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="workspace_id", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="updated_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="last_activity_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
    ),
    primary_key=("id",),
)


RUNTIME_SESSIONS_V1 = s.Table(
    name="runtime_sessions",
    columns=(
        s.ColumnDefinition(name="id", type="text", nullable=True),
        s.ColumnDefinition(name="bcn_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="channel_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="runtime", type="text", nullable=True),
        s.ColumnDefinition(name="runtime_version", type="text", nullable=True),
        s.ColumnDefinition(name="provider_thread_id", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="updated_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
    ),
    primary_key=("id",),
)


RUNTIME_TURNS_V1 = s.Table(
    name="runtime_turns",
    columns=(
        s.ColumnDefinition(name="turn_id", type="text", nullable=True),
        s.ColumnDefinition(name="session_id", type="text", nullable=True),
        s.ColumnDefinition(name="provider_turn_id", type="text", nullable=True),
        s.ColumnDefinition(name="client_user_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="state", type="text", nullable=True),
        s.ColumnDefinition(name="started_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="completed_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="last_event_name", type="text", nullable=True),
        s.ColumnDefinition(name="error_kind", type="text", nullable=True),
        s.ColumnDefinition(name="error_message", type="text", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
    ),
    primary_key=("turn_id",),
)


INBOUND_MESSAGES_V1 = s.Table(
    name="inbound_messages",
    columns=(
        s.ColumnDefinition(name="message_id", type="text", nullable=True),
        s.ColumnDefinition(name="seq", type="integer", nullable=True),
        s.ColumnDefinition(name="session_id", type="text", nullable=True),
        s.ColumnDefinition(name="channel_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="channel", type="text", nullable=True),
        s.ColumnDefinition(name="provider_thread_id", type="text", nullable=True),
        s.ColumnDefinition(name="provider_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="provider_time_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="received_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="sender", type="text", nullable=True),
        s.ColumnDefinition(name="message_type", type="text", nullable=True),
        s.ColumnDefinition(name="canonical_target", type="text", nullable=True),
        s.ColumnDefinition(name="target_kind", type="text", nullable=True),
        s.ColumnDefinition(
            name="reply_to_provider_message_id", type="text", nullable=True
        ),
        s.ColumnDefinition(name="body", type="text", nullable=True),
        s.ColumnDefinition(name="mentions_agent", type="integer", nullable=True),
        s.ColumnDefinition(name="notifies_runtime", type="integer", nullable=True),
        s.ColumnDefinition(name="provider_payload_ref", type="text", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
    ),
    primary_key=("message_id",),
)


INBOUND_ATTACHMENTS_V1 = s.Table(
    name="inbound_attachments",
    columns=(
        s.ColumnDefinition(name="attachment_id", type="text", nullable=True),
        s.ColumnDefinition(name="message_id", type="text", nullable=True),
        s.ColumnDefinition(name="ordinal", type="integer", nullable=True),
        s.ColumnDefinition(name="name", type="text", nullable=True),
        s.ColumnDefinition(name="kind", type="text", nullable=True),
        s.ColumnDefinition(name="state", type="text", nullable=True),
        s.ColumnDefinition(name="media_type", type="text", nullable=True),
        s.ColumnDefinition(name="relative_path", type="text", nullable=True),
        s.ColumnDefinition(name="size_bytes", type="integer", nullable=True),
        s.ColumnDefinition(name="error", type="text", nullable=True),
    ),
    primary_key=("attachment_id",),
)


OUTBOUND_MESSAGES_V1 = s.Table(
    name="outbound_messages",
    columns=(
        s.ColumnDefinition(name="outbound_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="command_id", type="text", nullable=True),
        s.ColumnDefinition(name="session_id", type="text", nullable=True),
        s.ColumnDefinition(name="channel_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="target", type="text", nullable=True),
        s.ColumnDefinition(name="reply_to_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="body", type="text", nullable=True),
        s.ColumnDefinition(name="state", type="text", nullable=True),
        s.ColumnDefinition(name="fresh_check_state", type="text", nullable=True),
        s.ColumnDefinition(name="snapshot_seq", type="integer", nullable=True),
        s.ColumnDefinition(name="current_inbound_seq", type="integer", nullable=True),
        s.ColumnDefinition(name="provider_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="provider_receipt_ref", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(
            name="provider_attempted_at_ms", type="integer", nullable=True
        ),
        s.ColumnDefinition(name="completed_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="draft_saved_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="error_kind", type="text", nullable=True),
        s.ColumnDefinition(name="error_message", type="text", nullable=True),
        s.ColumnDefinition(name="next_action", type="text", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
    ),
    primary_key=("outbound_message_id",),
)


CONSUMER_CURSORS_V1 = s.Table(
    name="consumer_cursors",
    columns=(
        s.ColumnDefinition(name="session_id", type="text", nullable=True),
        s.ColumnDefinition(name="delivered_through_seq", type="integer", nullable=True),
        s.ColumnDefinition(name="inbox_snapshot_seq", type="integer", nullable=True),
        s.ColumnDefinition(name="inbox_snapshot_source", type="text", nullable=True),
        s.ColumnDefinition(name="inbox_snapshot_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="last_check_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="last_read_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="updated_at_ms", type="integer", nullable=True),
    ),
    primary_key=("session_id",),
)


RUNTIME_EVENTS_V1 = s.Table(
    name="runtime_events",
    columns=(
        s.ColumnDefinition(
            name="event_seq", type="integer", nullable=True, generated=True
        ),
        s.ColumnDefinition(name="event_id", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="level", type="text", nullable=True),
        s.ColumnDefinition(name="event_name", type="text", nullable=True),
        s.ColumnDefinition(name="state", type="text", nullable=True),
        s.ColumnDefinition(name="duration_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="node_id", type="text", nullable=True),
        s.ColumnDefinition(name="channel", type="text", nullable=True),
        s.ColumnDefinition(name="runtime", type="text", nullable=True),
        s.ColumnDefinition(name="channel_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="bcn_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="runtime_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="turn_id", type="text", nullable=True),
        s.ColumnDefinition(name="request_id", type="text", nullable=True),
        s.ColumnDefinition(name="command_id", type="text", nullable=True),
        s.ColumnDefinition(name="inbound_seq", type="integer", nullable=True),
        s.ColumnDefinition(name="outbound_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="error_kind", type="text", nullable=True),
        s.ColumnDefinition(name="error_type", type="text", nullable=True),
        s.ColumnDefinition(name="error_message", type="text", nullable=True),
        s.ColumnDefinition(name="traceback_ref", type="text", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
    ),
    primary_key=("event_seq",),
)


IDX_OUTBOUND_SESSION_CREATED_V1 = s.Index(
    name="idx_outbound_session_created", columns=("session_id", "created_at_ms")
)


IDX_OUTBOUND_STATE_CREATED_V1 = s.Index(
    name="idx_outbound_state_created", columns=("state", "created_at_ms")
)


RUNTIME_ATTEMPTS_V4 = s.Table(
    name="runtime_attempts",
    columns=(
        s.ColumnDefinition(name="turn_id", type="text", nullable=True),
        s.ColumnDefinition(name="session_id", type="text", nullable=True),
        s.ColumnDefinition(name="client_user_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="started_at_ms", type="integer", nullable=True),
    ),
    primary_key=("turn_id",),
)


REMINDERS_V12 = s.Table(
    name="reminders",
    columns=(
        s.ColumnDefinition(name="reminder_id", type="text", nullable=True),
        s.ColumnDefinition(name="owner_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="anchor_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="title", type="text", nullable=True),
        s.ColumnDefinition(name="state", type="text", nullable=True),
        s.ColumnDefinition(name="next_fire_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="repeat_rule", type="text", nullable=True),
        s.ColumnDefinition(name="timezone", type="text", nullable=True),
        s.ColumnDefinition(name="revision", type="integer", nullable=True),
        s.ColumnDefinition(name="last_occurrence_no", type="integer", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="updated_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="last_fired_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="canceled_at_ms", type="integer", nullable=True),
    ),
    primary_key=("reminder_id",),
)


REMINDER_OCCURRENCES_V12 = s.Table(
    name="reminder_occurrences",
    columns=(
        s.ColumnDefinition(name="occurrence_id", type="text", nullable=True),
        s.ColumnDefinition(name="reminder_id", type="text", nullable=True),
        s.ColumnDefinition(name="owner_session_id", type="text", nullable=True),
        s.ColumnDefinition(name="occurrence_no", type="integer", nullable=True),
        s.ColumnDefinition(name="anchor_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="scheduled_for_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="fired_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="next_fire_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="overdue", type="integer", nullable=True),
        s.ColumnDefinition(name="read_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
    ),
    primary_key=("occurrence_id",),
)


IDX_REMINDERS_STATE_NEXT_V12 = s.Index(
    name="idx_reminders_state_next", columns=("state", "next_fire_at_ms", "reminder_id")
)


IDX_CHANNEL_SESSIONS_PROVIDER_IDENTITY_V13 = s.Index(
    name="idx_channel_sessions_provider_identity",
    columns=("agent_id", "channel", "provider_thread_id"),
)


IDX_RUNTIME_ATTEMPTS_SESSION_STARTED_V13 = s.Index(
    name="idx_runtime_attempts_session_started",
    columns=("agent_id", "session_id", "started_at_ms"),
)


HANDOFFS_V15 = s.Table(
    name="handoffs",
    columns=(
        s.ColumnDefinition(name="seq", type="integer", nullable=True, generated=True),
        s.ColumnDefinition(name="handoff_id", type="text", unique=True),
        s.ColumnDefinition(name="command_id", type="text", unique=True),
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="source_session_id", type="text"),
        s.ColumnDefinition(name="target_session_id", type="text"),
        s.ColumnDefinition(name="source_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="body", type="text"),
        s.ColumnDefinition(name="created_at_ms", type="integer"),
        s.ColumnDefinition(name="read_at_ms", type="integer", nullable=True),
    ),
    primary_key=("seq",),
)


OUTBOUND_MESSAGES_PROVIDER_ATTEMPTS_V16 = s.Table(
    name="_outbound_messages_provider_attempts",
    columns=(
        s.ColumnDefinition(name="outbound_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="command_id", type="text"),
        s.ColumnDefinition(name="session_id", type="text"),
        s.ColumnDefinition(name="channel_session_id", type="text"),
        s.ColumnDefinition(name="target", type="text"),
        s.ColumnDefinition(name="reply_to_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="body", type="text"),
        s.ColumnDefinition(
            name="state",
            type="text",
            check=s.In(
                expression=s.Column(name="state"),
                values=("pending", "queued", "sent", "partial", "failed", "unknown"),
            ),
        ),
        s.ColumnDefinition(name="snapshot_seq", type="integer"),
        s.ColumnDefinition(name="current_inbound_seq", type="integer"),
        s.ColumnDefinition(name="provider_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="provider_receipt_ref", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer"),
        s.ColumnDefinition(name="provider_attempted_at_ms", type="integer"),
        s.ColumnDefinition(name="completed_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="error_kind", type="text", nullable=True),
        s.ColumnDefinition(name="error_message", type="text", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
        s.ColumnDefinition(name="attachments_json", type="text", default="[]"),
        s.ColumnDefinition(name="agent_id", type="text", nullable=True),
        s.ColumnDefinition(name="agent_name", type="text", nullable=True),
    ),
    primary_key=("outbound_message_id",),
)


MESSAGES_V18 = s.Table(
    name="_messages_v18",
    columns=(
        s.ColumnDefinition(name="message_id", type="text", nullable=True),
        s.ColumnDefinition(
            name="seq",
            type="integer",
            unique=True,
            check=s.Compare(
                left=s.Column(name="seq"), operator=">", right=s.Value(value=0)
            ),
        ),
        s.ColumnDefinition(
            name="direction",
            type="text",
            check=s.In(
                expression=s.Column(name="direction"), values=("inbound", "outbound")
            ),
        ),
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="session_id", type="text"),
        s.ColumnDefinition(name="channel_session_id", type="text"),
        s.ColumnDefinition(name="channel", type="text"),
        s.ColumnDefinition(name="provider_thread_id", type="text"),
        s.ColumnDefinition(name="provider_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="provider_time_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="received_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="sender", type="text", nullable=True),
        s.ColumnDefinition(name="message_type", type="text"),
        s.ColumnDefinition(name="target", type="text"),
        s.ColumnDefinition(
            name="target_kind",
            type="text",
            check=s.In(expression=s.Column(name="target_kind"), values=("dm", "group")),
        ),
        s.ColumnDefinition(name="reply_to_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="body", type="text"),
        s.ColumnDefinition(
            name="mentions_agent",
            type="integer",
            nullable=True,
            check=s.In(expression=s.Column(name="mentions_agent"), values=(0, 1)),
        ),
        s.ColumnDefinition(
            name="notifies_runtime",
            type="integer",
            nullable=True,
            check=s.In(expression=s.Column(name="notifies_runtime"), values=(0, 1)),
        ),
        s.ColumnDefinition(name="provider_payload_ref", type="text", nullable=True),
        s.ColumnDefinition(name="command_id", type="text", nullable=True),
        s.ColumnDefinition(
            name="delivery_state",
            type="text",
            nullable=True,
            check=s.In(
                expression=s.Column(name="delivery_state"),
                values=("pending", "queued", "sent", "partial", "failed", "unknown"),
            ),
        ),
        s.ColumnDefinition(name="snapshot_seq", type="integer", nullable=True),
        s.ColumnDefinition(name="current_inbound_seq", type="integer", nullable=True),
        s.ColumnDefinition(name="provider_receipt_ref", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(
            name="provider_attempted_at_ms", type="integer", nullable=True
        ),
        s.ColumnDefinition(name="completed_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="error_kind", type="text", nullable=True),
        s.ColumnDefinition(name="error_message", type="text", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
        s.ColumnDefinition(name="attachments_json", type="text", nullable=True),
    ),
    primary_key=("message_id",),
    checks=(
        s.AnyOf(
            conditions=(
                s.All(
                    conditions=(
                        s.Compare(
                            left=s.Column(name="direction"),
                            operator="=",
                            right=s.Value(value="inbound"),
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="provider_message_id")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="received_at_ms")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="mentions_agent")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="notifies_runtime")
                            )
                        ),
                        s.IsNull(expression=s.Column(name="command_id")),
                        s.IsNull(expression=s.Column(name="delivery_state")),
                        s.IsNull(expression=s.Column(name="snapshot_seq")),
                        s.IsNull(expression=s.Column(name="current_inbound_seq")),
                        s.IsNull(expression=s.Column(name="provider_receipt_ref")),
                        s.IsNull(expression=s.Column(name="created_at_ms")),
                        s.IsNull(expression=s.Column(name="provider_attempted_at_ms")),
                        s.IsNull(expression=s.Column(name="completed_at_ms")),
                        s.IsNull(expression=s.Column(name="error_kind")),
                        s.IsNull(expression=s.Column(name="error_message")),
                        s.IsNull(expression=s.Column(name="attachments_json")),
                    )
                ),
                s.All(
                    conditions=(
                        s.Compare(
                            left=s.Column(name="direction"),
                            operator="=",
                            right=s.Value(value="outbound"),
                        ),
                        s.Not(
                            condition=s.IsNull(expression=s.Column(name="command_id"))
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="delivery_state")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(expression=s.Column(name="snapshot_seq"))
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="current_inbound_seq")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="created_at_ms")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="provider_attempted_at_ms")
                            )
                        ),
                        s.IsNull(expression=s.Column(name="received_at_ms")),
                        s.IsNull(expression=s.Column(name="mentions_agent")),
                        s.IsNull(expression=s.Column(name="notifies_runtime")),
                        s.IsNull(expression=s.Column(name="provider_payload_ref")),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="attachments_json")
                            )
                        ),
                    )
                ),
            )
        ),
    ),
)


IDX_MESSAGES_AGENT_SESSION_TARGET_SEQ_V18 = s.Index(
    name="idx_messages_agent_session_target_seq",
    columns=("agent_id", "session_id", "target", "seq"),
)


IDX_MESSAGES_AGENT_DIRECTION_SEQ_V18 = s.Index(
    name="idx_messages_agent_direction_seq", columns=("agent_id", "direction", "seq")
)


IDX_MESSAGES_INBOUND_PROVIDER_IDENTITY_V18 = s.Index(
    name="idx_messages_inbound_provider_identity",
    columns=("agent_id", "channel", "provider_thread_id", "provider_message_id"),
    unique=True,
    where=s.Compare(
        left=s.Column(name="direction"), operator="=", right=s.Value(value="inbound")
    ),
)


IDX_MESSAGES_OUTBOUND_COMMAND_V18 = s.Index(
    name="idx_messages_outbound_command",
    columns=("agent_id", "command_id"),
    unique=True,
    where=s.Compare(
        left=s.Column(name="direction"), operator="=", right=s.Value(value="outbound")
    ),
)


IDX_MESSAGES_OUTBOUND_STATE_CREATED_V18 = s.Index(
    name="idx_messages_outbound_state_created",
    columns=("agent_id", "delivery_state", "created_at_ms"),
    where=s.Compare(
        left=s.Column(name="direction"), operator="=", right=s.Value(value="outbound")
    ),
)


IDX_MESSAGES_REPLY_TO_MESSAGE_V18 = s.Index(
    name="idx_messages_reply_to_message", columns=("reply_to_message_id",)
)


MESSAGES_V19 = s.Table(
    name="messages",
    columns=(
        s.ColumnDefinition(name="message_id", type="text", nullable=True),
        s.ColumnDefinition(
            name="seq",
            type="integer",
            unique=True,
            check=s.Compare(
                left=s.Column(name="seq"), operator=">", right=s.Value(value=0)
            ),
        ),
        s.ColumnDefinition(
            name="direction",
            type="text",
            check=s.In(
                expression=s.Column(name="direction"), values=("inbound", "outbound")
            ),
        ),
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="session_id", type="text"),
        s.ColumnDefinition(name="channel_session_id", type="text"),
        s.ColumnDefinition(name="channel", type="text"),
        s.ColumnDefinition(name="provider_thread_id", type="text"),
        s.ColumnDefinition(name="provider_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="provider_time_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="received_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="sender", type="text", nullable=True),
        s.ColumnDefinition(name="message_type", type="text"),
        s.ColumnDefinition(name="target", type="text"),
        s.ColumnDefinition(
            name="target_kind",
            type="text",
            check=s.In(expression=s.Column(name="target_kind"), values=("dm", "group")),
        ),
        s.ColumnDefinition(name="reply_to_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="body", type="text"),
        s.ColumnDefinition(
            name="mentions_agent",
            type="integer",
            nullable=True,
            check=s.In(expression=s.Column(name="mentions_agent"), values=(0, 1)),
        ),
        s.ColumnDefinition(
            name="notifies_runtime",
            type="integer",
            nullable=True,
            check=s.In(expression=s.Column(name="notifies_runtime"), values=(0, 1)),
        ),
        s.ColumnDefinition(name="provider_payload_ref", type="text", nullable=True),
        s.ColumnDefinition(name="command_id", type="text", nullable=True),
        s.ColumnDefinition(
            name="delivery_state",
            type="text",
            nullable=True,
            check=s.In(
                expression=s.Column(name="delivery_state"),
                values=("pending", "queued", "sent", "partial", "failed", "unknown"),
            ),
        ),
        s.ColumnDefinition(name="snapshot_seq", type="integer", nullable=True),
        s.ColumnDefinition(name="current_inbound_seq", type="integer", nullable=True),
        s.ColumnDefinition(name="provider_receipt_ref", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(
            name="provider_attempted_at_ms", type="integer", nullable=True
        ),
        s.ColumnDefinition(name="completed_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="error_kind", type="text", nullable=True),
        s.ColumnDefinition(name="error_message", type="text", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
        s.ColumnDefinition(name="attachments_json", type="text", nullable=True),
    ),
    primary_key=("message_id",),
    checks=(
        s.AnyOf(
            conditions=(
                s.All(
                    conditions=(
                        s.Compare(
                            left=s.Column(name="direction"),
                            operator="=",
                            right=s.Value(value="inbound"),
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="received_at_ms")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="mentions_agent")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="notifies_runtime")
                            )
                        ),
                        s.IsNull(expression=s.Column(name="command_id")),
                        s.IsNull(expression=s.Column(name="delivery_state")),
                        s.IsNull(expression=s.Column(name="snapshot_seq")),
                        s.IsNull(expression=s.Column(name="current_inbound_seq")),
                        s.IsNull(expression=s.Column(name="provider_receipt_ref")),
                        s.IsNull(expression=s.Column(name="created_at_ms")),
                        s.IsNull(expression=s.Column(name="provider_attempted_at_ms")),
                        s.IsNull(expression=s.Column(name="completed_at_ms")),
                        s.IsNull(expression=s.Column(name="error_kind")),
                        s.IsNull(expression=s.Column(name="error_message")),
                        s.IsNull(expression=s.Column(name="attachments_json")),
                    )
                ),
                s.All(
                    conditions=(
                        s.Compare(
                            left=s.Column(name="direction"),
                            operator="=",
                            right=s.Value(value="outbound"),
                        ),
                        s.Not(
                            condition=s.IsNull(expression=s.Column(name="command_id"))
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="delivery_state")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(expression=s.Column(name="snapshot_seq"))
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="current_inbound_seq")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="created_at_ms")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="provider_attempted_at_ms")
                            )
                        ),
                        s.IsNull(expression=s.Column(name="received_at_ms")),
                        s.IsNull(expression=s.Column(name="mentions_agent")),
                        s.IsNull(expression=s.Column(name="notifies_runtime")),
                        s.IsNull(expression=s.Column(name="provider_payload_ref")),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="attachments_json")
                            )
                        ),
                    )
                ),
            )
        ),
    ),
)


MESSAGES_V20 = s.Table(
    name="messages",
    columns=(
        s.ColumnDefinition(name="message_id", type="text", nullable=True),
        s.ColumnDefinition(
            name="seq",
            type="integer",
            unique=True,
            check=s.Compare(
                left=s.Column(name="seq"), operator=">", right=s.Value(value=0)
            ),
        ),
        s.ColumnDefinition(
            name="direction",
            type="text",
            check=s.In(
                expression=s.Column(name="direction"), values=("inbound", "outbound")
            ),
        ),
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="session_id", type="text"),
        s.ColumnDefinition(name="channel_session_id", type="text"),
        s.ColumnDefinition(name="channel", type="text"),
        s.ColumnDefinition(name="provider_thread_id", type="text"),
        s.ColumnDefinition(name="provider_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="provider_time_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="received_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="sender", type="text", nullable=True),
        s.ColumnDefinition(name="message_type", type="text"),
        s.ColumnDefinition(name="target", type="text"),
        s.ColumnDefinition(
            name="target_kind",
            type="text",
            check=s.In(expression=s.Column(name="target_kind"), values=("dm", "group")),
        ),
        s.ColumnDefinition(name="reply_to_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="body", type="text"),
        s.ColumnDefinition(
            name="mentions_agent",
            type="integer",
            nullable=True,
            check=s.In(expression=s.Column(name="mentions_agent"), values=(0, 1)),
        ),
        s.ColumnDefinition(
            name="notifies_runtime",
            type="integer",
            nullable=True,
            check=s.In(expression=s.Column(name="notifies_runtime"), values=(0, 1)),
        ),
        s.ColumnDefinition(name="provider_payload_ref", type="text", nullable=True),
        s.ColumnDefinition(name="command_id", type="text", nullable=True),
        s.ColumnDefinition(
            name="delivery_state",
            type="text",
            nullable=True,
            check=s.In(
                expression=s.Column(name="delivery_state"),
                values=("pending", "queued", "sent", "partial", "failed", "unknown"),
            ),
        ),
        s.ColumnDefinition(name="provider_receipt_ref", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(
            name="provider_attempted_at_ms", type="integer", nullable=True
        ),
        s.ColumnDefinition(name="completed_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="error_kind", type="text", nullable=True),
        s.ColumnDefinition(name="error_message", type="text", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
        s.ColumnDefinition(name="attachments_json", type="text", nullable=True),
    ),
    primary_key=("message_id",),
    checks=(
        s.AnyOf(
            conditions=(
                s.All(
                    conditions=(
                        s.Compare(
                            left=s.Column(name="direction"),
                            operator="=",
                            right=s.Value(value="inbound"),
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="received_at_ms")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="mentions_agent")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="notifies_runtime")
                            )
                        ),
                        s.IsNull(expression=s.Column(name="command_id")),
                        s.IsNull(expression=s.Column(name="delivery_state")),
                        s.IsNull(expression=s.Column(name="provider_receipt_ref")),
                        s.IsNull(expression=s.Column(name="created_at_ms")),
                        s.IsNull(expression=s.Column(name="provider_attempted_at_ms")),
                        s.IsNull(expression=s.Column(name="completed_at_ms")),
                        s.IsNull(expression=s.Column(name="error_kind")),
                        s.IsNull(expression=s.Column(name="error_message")),
                        s.IsNull(expression=s.Column(name="attachments_json")),
                    )
                ),
                s.All(
                    conditions=(
                        s.Compare(
                            left=s.Column(name="direction"),
                            operator="=",
                            right=s.Value(value="outbound"),
                        ),
                        s.Not(
                            condition=s.IsNull(expression=s.Column(name="command_id"))
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="delivery_state")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="created_at_ms")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="provider_attempted_at_ms")
                            )
                        ),
                        s.IsNull(expression=s.Column(name="received_at_ms")),
                        s.IsNull(expression=s.Column(name="mentions_agent")),
                        s.IsNull(expression=s.Column(name="notifies_runtime")),
                        s.IsNull(expression=s.Column(name="provider_payload_ref")),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="attachments_json")
                            )
                        ),
                    )
                ),
            )
        ),
    ),
)


CONSUMER_CURSORS_V20 = s.Table(
    name="consumer_cursors",
    columns=(
        s.ColumnDefinition(name="session_id", type="text", nullable=True),
        s.ColumnDefinition(name="delivered_through_seq", type="integer"),
        s.ColumnDefinition(name="last_check_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="last_read_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="updated_at_ms", type="integer"),
    ),
    primary_key=("session_id",),
)


IDX_CHANNEL_SESSIONS_TARGET_HANDLE_V22 = s.Index(
    name="idx_channel_sessions_target_handle",
    columns=("agent_id", "target_kind", "target_handle_key"),
    where=s.Not(condition=s.IsNull(expression=s.Column(name="target_handle_key"))),
)


IDX_THREADS_CHANNEL_V25 = s.Index(
    name="idx_threads_channel", columns=("agent_id", "channel_session_id")
)


IDX_MESSAGES_AGENT_THREAD_TARGET_SEQ_V25 = s.Index(
    name="idx_messages_agent_thread_target_seq",
    columns=("agent_id", "thread_id", "target", "seq"),
)


IDX_REMINDERS_OWNER_STATE_UPDATED_V25 = s.Index(
    name="idx_reminders_owner_state_updated",
    columns=("agent_id", "owner_thread_id", "state", "updated_at_ms", "reminder_id"),
)


MESSAGES_V30 = s.Table(
    name="messages",
    columns=(
        s.ColumnDefinition(name="message_id", type="text", nullable=True),
        s.ColumnDefinition(
            name="seq",
            type="integer",
            unique=True,
            check=s.Compare(
                left=s.Column(name="seq"), operator=">", right=s.Value(value=0)
            ),
        ),
        s.ColumnDefinition(
            name="direction",
            type="text",
            check=s.In(
                expression=s.Column(name="direction"), values=("inbound", "outbound")
            ),
        ),
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="thread_id", type="text"),
        s.ColumnDefinition(name="channel_session_id", type="text"),
        s.ColumnDefinition(name="channel", type="text"),
        s.ColumnDefinition(name="provider_thread_id", type="text"),
        s.ColumnDefinition(name="provider_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="provider_time_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="received_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="sender", type="text", nullable=True),
        s.ColumnDefinition(name="message_type", type="text"),
        s.ColumnDefinition(name="target", type="text"),
        s.ColumnDefinition(
            name="target_kind",
            type="text",
            check=s.In(expression=s.Column(name="target_kind"), values=("dm", "group")),
        ),
        s.ColumnDefinition(name="reply_to_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="body", type="text"),
        s.ColumnDefinition(
            name="mentions_agent",
            type="integer",
            nullable=True,
            check=s.In(expression=s.Column(name="mentions_agent"), values=(0, 1)),
        ),
        s.ColumnDefinition(
            name="notifies_runtime",
            type="integer",
            nullable=True,
            check=s.In(expression=s.Column(name="notifies_runtime"), values=(0, 1)),
        ),
        s.ColumnDefinition(name="provider_payload_ref", type="text", nullable=True),
        s.ColumnDefinition(
            name="delivery_state",
            type="text",
            nullable=True,
            check=s.In(
                expression=s.Column(name="delivery_state"),
                values=("pending", "queued", "sent", "partial", "failed", "unknown"),
            ),
        ),
        s.ColumnDefinition(name="provider_receipt_ref", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(
            name="provider_attempted_at_ms", type="integer", nullable=True
        ),
        s.ColumnDefinition(name="completed_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="error_kind", type="text", nullable=True),
        s.ColumnDefinition(name="error_message", type="text", nullable=True),
        s.ColumnDefinition(name="metadata_json", type="text", nullable=True),
        s.ColumnDefinition(name="attachments_json", type="text", nullable=True),
        s.ColumnDefinition(name="sender_id", type="text", nullable=True),
        s.ColumnDefinition(name="sender_display_name", type="text", nullable=True),
    ),
    primary_key=("message_id",),
    checks=(
        s.AnyOf(
            conditions=(
                s.All(
                    conditions=(
                        s.Compare(
                            left=s.Column(name="direction"),
                            operator="=",
                            right=s.Value(value="inbound"),
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="received_at_ms")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="mentions_agent")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="notifies_runtime")
                            )
                        ),
                        s.IsNull(expression=s.Column(name="delivery_state")),
                        s.IsNull(expression=s.Column(name="provider_receipt_ref")),
                        s.IsNull(expression=s.Column(name="created_at_ms")),
                        s.IsNull(expression=s.Column(name="provider_attempted_at_ms")),
                        s.IsNull(expression=s.Column(name="completed_at_ms")),
                        s.IsNull(expression=s.Column(name="error_kind")),
                        s.IsNull(expression=s.Column(name="error_message")),
                        s.IsNull(expression=s.Column(name="attachments_json")),
                    )
                ),
                s.All(
                    conditions=(
                        s.Compare(
                            left=s.Column(name="direction"),
                            operator="=",
                            right=s.Value(value="outbound"),
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="delivery_state")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="created_at_ms")
                            )
                        ),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="provider_attempted_at_ms")
                            )
                        ),
                        s.IsNull(expression=s.Column(name="received_at_ms")),
                        s.IsNull(expression=s.Column(name="mentions_agent")),
                        s.IsNull(expression=s.Column(name="notifies_runtime")),
                        s.IsNull(expression=s.Column(name="provider_payload_ref")),
                        s.Not(
                            condition=s.IsNull(
                                expression=s.Column(name="attachments_json")
                            )
                        ),
                    )
                ),
            )
        ),
    ),
)


SETTINGS_V31 = s.Table(
    name="settings",
    columns=(
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="key", type="text"),
        s.ColumnDefinition(name="value", type="text"),
        s.ColumnDefinition(name="updated_at_ms", type="integer"),
    ),
    primary_key=("agent_id", "key"),
)


MESSAGE_SEARCH_V32 = s.Index(
    name="message_search", columns=("body",), kind="search", row_key="seq"
)


IDX_MESSAGES_AGENT_SEARCH_TIME_V32 = s.Index(
    name="idx_messages_agent_search_time",
    order_by=(
        s.Sort(expression=s.Column(name="agent_id"), nulls="first"),
        s.Sort(
            expression=s.Call(
                name="coalesce",
                arguments=(
                    s.Column(name="provider_time_ms"),
                    s.Column(name="received_at_ms"),
                    s.Column(name="created_at_ms"),
                ),
            ),
            nulls="first",
        ),
        s.Sort(expression=s.Column(name="seq"), nulls="first"),
    ),
)
