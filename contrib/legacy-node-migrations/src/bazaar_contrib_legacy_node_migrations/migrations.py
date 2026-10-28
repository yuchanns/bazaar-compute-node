"""Business-owned schema evolution through the public migration contract."""

from __future__ import annotations

import bazaar_contrib_storage as s
from bazaar_contrib_schema import Migration, ModuleSchema, Progress

from . import definitions, transforms


async def runtime_attempt_facts(session: s.Session) -> None:
    await session.apply_schema((s.CreateTable(table=definitions.RUNTIME_ATTEMPTS_V4),))
    await session.copy(
        s.Copy(
            table="runtime_attempts",
            query=s.Select(
                table="runtime_turns",
                columns={
                    "turn_id": s.Column(name="turn_id"),
                    "session_id": s.Column(name="session_id"),
                    "client_user_message_id": s.Column(name="client_user_message_id"),
                    "started_at_ms": s.Column(name="started_at_ms"),
                },
                where=s.Not(
                    condition=s.IsNull(
                        expression=s.Column(name="client_user_message_id")
                    )
                ),
            ),
        )
    )
    await session.apply_schema(
        (
            s.DropIndex(name="idx_runtime_turns_session_state", table="runtime_turns"),
            s.DropTable(table="runtime_turns"),
            s.CreateIndex(
                table="runtime_attempts",
                index=s.Index(
                    name="idx_runtime_attempts_session_started",
                    columns=("session_id", "started_at_ms"),
                ),
            ),
        )
    )


async def inbound_message_references(session: s.Session) -> None:
    await session.apply_schema(
        (
            s.RenameColumn(
                table="inbound_messages",
                column="reply_to_provider_message_id",
                name="reply_to_message_id",
            ),
        )
    )
    await transforms.resolve_references(session)
    await session.apply_schema(
        (
            s.CreateIndex(
                table="inbound_messages",
                index=s.Index(
                    name="idx_inbound_reply_to_message",
                    columns=("reply_to_message_id",),
                ),
            ),
        )
    )


async def add_agent_ownership(session: s.Session) -> None:
    owner = await transforms.workspace_owner(session)
    await session.apply_schema(
        (
            s.AddColumn(
                table="channel_sessions",
                column=s.ColumnDefinition(name="agent_id", type="text", nullable=True),
            ),
            s.AddColumn(
                table="bcn_sessions",
                column=s.ColumnDefinition(name="agent_id", type="text", nullable=True),
            ),
            s.AddColumn(
                table="inbound_messages",
                column=s.ColumnDefinition(name="agent_id", type="text", nullable=True),
            ),
            s.AddColumn(
                table="outbound_messages",
                column=s.ColumnDefinition(name="agent_id", type="text", nullable=True),
            ),
            s.AddColumn(
                table="outbound_messages",
                column=s.ColumnDefinition(
                    name="agent_name", type="text", nullable=True
                ),
            ),
            s.AddColumn(
                table="runtime_attempts",
                column=s.ColumnDefinition(name="agent_id", type="text", nullable=True),
            ),
            s.AddColumn(
                table="reminders",
                column=s.ColumnDefinition(name="agent_id", type="text", nullable=True),
            ),
            s.AddColumn(
                table="reminder_occurrences",
                column=s.ColumnDefinition(name="agent_id", type="text", nullable=True),
            ),
        )
    )
    await session.update(
        s.Update(table="channel_sessions", values={"agent_id": s.Value(value=owner)})
    )
    await session.update(
        s.Update(table="bcn_sessions", values={"agent_id": s.Value(value=owner)})
    )
    await session.update(
        s.Update(table="inbound_messages", values={"agent_id": s.Value(value=owner)})
    )
    await session.update(
        s.Update(
            table="outbound_messages",
            values={
                "agent_id": s.Value(value=owner),
                "agent_name": s.Value(value="default"),
            },
        )
    )
    await session.update(
        s.Update(table="runtime_attempts", values={"agent_id": s.Value(value=owner)})
    )
    await session.update(
        s.Update(table="reminders", values={"agent_id": s.Value(value=owner)})
    )
    await session.update(
        s.Update(
            table="reminder_occurrences", values={"agent_id": s.Value(value=owner)}
        )
    )
    await session.apply_schema(
        (
            s.DropIndex(
                name="idx_channel_sessions_provider_identity", table="channel_sessions"
            ),
            s.CreateIndex(
                table="channel_sessions",
                index=definitions.IDX_CHANNEL_SESSIONS_PROVIDER_IDENTITY_V13,
            ),
            s.DropIndex(name="idx_bcn_sessions_channel", table="bcn_sessions"),
            s.CreateIndex(
                table="bcn_sessions",
                index=s.Index(
                    name="idx_bcn_sessions_channel",
                    columns=("agent_id", "channel_session_id"),
                ),
            ),
            s.DropIndex(name="idx_inbound_provider_identity", table="inbound_messages"),
            s.CreateIndex(
                table="inbound_messages",
                index=s.Index(
                    name="idx_inbound_provider_identity",
                    columns=(
                        "agent_id",
                        "channel",
                        "provider_thread_id",
                        "provider_message_id",
                    ),
                    unique=True,
                ),
            ),
            s.DropIndex(
                name="idx_runtime_attempts_session_started", table="runtime_attempts"
            ),
            s.CreateIndex(
                table="runtime_attempts",
                index=definitions.IDX_RUNTIME_ATTEMPTS_SESSION_STARTED_V13,
            ),
            s.DropIndex(name="idx_reminders_owner_state_updated", table="reminders"),
            s.CreateIndex(
                table="reminders",
                index=s.Index(
                    name="idx_reminders_owner_state_updated",
                    columns=(
                        "agent_id",
                        "owner_session_id",
                        "state",
                        "updated_at_ms",
                        "reminder_id",
                    ),
                ),
            ),
            s.DropIndex(
                name="idx_reminder_occurrences_owner_read_fired",
                table="reminder_occurrences",
            ),
            s.CreateIndex(
                table="reminder_occurrences",
                index=s.Index(
                    name="idx_reminder_occurrences_owner_read_fired",
                    columns=(
                        "agent_id",
                        "owner_session_id",
                        "read_at_ms",
                        "fired_at_ms",
                        "occurrence_id",
                    ),
                ),
            ),
            s.CreateTrigger(
                name="set_channel_sessions_agent_id",
                table="channel_sessions",
                event="insert",
                when=s.IsNull(expression=s.Column(name="agent_id", table="new")),
                action=s.Update(
                    table="channel_sessions",
                    values={"agent_id": s.Value(value=owner)},
                    where=s.Compare(
                        left=s.Column(name="id"),
                        operator="=",
                        right=s.Column(name="id", table="new"),
                    ),
                ),
            ),
            s.CreateTrigger(
                name="set_bcn_sessions_agent_id",
                table="bcn_sessions",
                event="insert",
                when=s.IsNull(expression=s.Column(name="agent_id", table="new")),
                action=s.Update(
                    table="bcn_sessions",
                    values={"agent_id": s.Value(value=owner)},
                    where=s.Compare(
                        left=s.Column(name="id"),
                        operator="=",
                        right=s.Column(name="id", table="new"),
                    ),
                ),
            ),
            s.CreateTrigger(
                name="set_inbound_messages_agent_id",
                table="inbound_messages",
                event="insert",
                when=s.IsNull(expression=s.Column(name="agent_id", table="new")),
                action=s.Update(
                    table="inbound_messages",
                    values={"agent_id": s.Value(value=owner)},
                    where=s.Compare(
                        left=s.Column(name="message_id"),
                        operator="=",
                        right=s.Column(name="message_id", table="new"),
                    ),
                ),
            ),
            s.CreateTrigger(
                name="set_outbound_messages_agent_identity",
                table="outbound_messages",
                event="insert",
                when=s.AnyOf(
                    conditions=(
                        s.IsNull(expression=s.Column(name="agent_id", table="new")),
                        s.IsNull(expression=s.Column(name="agent_name", table="new")),
                    )
                ),
                action=s.Update(
                    table="outbound_messages",
                    values={
                        "agent_id": s.Call(
                            name="coalesce",
                            arguments=(
                                s.Column(name="agent_id", table="new"),
                                s.Value(value=owner),
                            ),
                        ),
                        "agent_name": s.Call(
                            name="coalesce",
                            arguments=(
                                s.Column(name="agent_name", table="new"),
                                s.Value(value="default"),
                            ),
                        ),
                    },
                    where=s.Compare(
                        left=s.Column(name="outbound_message_id"),
                        operator="=",
                        right=s.Column(name="outbound_message_id", table="new"),
                    ),
                ),
            ),
            s.CreateTrigger(
                name="set_runtime_attempts_agent_id",
                table="runtime_attempts",
                event="insert",
                when=s.IsNull(expression=s.Column(name="agent_id", table="new")),
                action=s.Update(
                    table="runtime_attempts",
                    values={"agent_id": s.Value(value=owner)},
                    where=s.Compare(
                        left=s.Column(name="turn_id"),
                        operator="=",
                        right=s.Column(name="turn_id", table="new"),
                    ),
                ),
            ),
            s.CreateTrigger(
                name="set_reminders_agent_id",
                table="reminders",
                event="insert",
                when=s.IsNull(expression=s.Column(name="agent_id", table="new")),
                action=s.Update(
                    table="reminders",
                    values={"agent_id": s.Value(value=owner)},
                    where=s.Compare(
                        left=s.Column(name="reminder_id"),
                        operator="=",
                        right=s.Column(name="reminder_id", table="new"),
                    ),
                ),
            ),
            s.CreateTrigger(
                name="set_reminder_occurrences_agent_id",
                table="reminder_occurrences",
                event="insert",
                when=s.IsNull(expression=s.Column(name="agent_id", table="new")),
                action=s.Update(
                    table="reminder_occurrences",
                    values={"agent_id": s.Value(value=owner)},
                    where=s.Compare(
                        left=s.Column(name="occurrence_id"),
                        operator="=",
                        right=s.Column(name="occurrence_id", table="new"),
                    ),
                ),
            ),
            s.DropTable(table="node_state"),
        )
    )


async def remove_outbound_drafts(session: s.Session) -> None:
    owner = await transforms.existing_owner(session)
    await session.apply_schema(
        (
            s.DropTrigger(
                name="set_outbound_messages_agent_identity", table="outbound_messages"
            ),
            s.DropIndex(name="idx_outbound_session_created", table="outbound_messages"),
            s.DropIndex(name="idx_outbound_state_created", table="outbound_messages"),
            s.CreateTable(table=definitions.OUTBOUND_MESSAGES_PROVIDER_ATTEMPTS_V16),
        )
    )
    await session.copy(
        s.Copy(
            table="_outbound_messages_provider_attempts",
            query=s.Select(
                table="outbound_messages",
                columns={
                    "outbound_message_id": s.Column(name="outbound_message_id"),
                    "command_id": s.Column(name="command_id"),
                    "session_id": s.Column(name="session_id"),
                    "channel_session_id": s.Column(name="channel_session_id"),
                    "target": s.Column(name="target"),
                    "reply_to_message_id": s.Column(name="reply_to_message_id"),
                    "body": s.Column(name="body"),
                    "state": s.Column(name="state"),
                    "snapshot_seq": s.Column(name="snapshot_seq"),
                    "current_inbound_seq": s.Column(name="current_inbound_seq"),
                    "provider_message_id": s.Column(name="provider_message_id"),
                    "provider_receipt_ref": s.Column(name="provider_receipt_ref"),
                    "created_at_ms": s.Column(name="created_at_ms"),
                    "provider_attempted_at_ms": s.Column(
                        name="provider_attempted_at_ms"
                    ),
                    "completed_at_ms": s.Column(name="completed_at_ms"),
                    "error_kind": s.Column(name="error_kind"),
                    "error_message": s.Column(name="error_message"),
                    "metadata_json": s.Column(name="metadata_json"),
                    "attachments_json": s.Column(name="attachments_json"),
                    "agent_id": s.Column(name="agent_id"),
                    "agent_name": s.Column(name="agent_name"),
                },
                where=s.In(
                    expression=s.Column(name="state"),
                    values=(
                        "pending",
                        "queued",
                        "sent",
                        "partial",
                        "failed",
                        "unknown",
                    ),
                ),
            ),
        )
    )
    await session.apply_schema(
        (
            s.DropTable(table="outbound_messages"),
            s.RenameTable(
                table="_outbound_messages_provider_attempts", name="outbound_messages"
            ),
            s.CreateIndex(
                table="outbound_messages",
                index=definitions.IDX_OUTBOUND_SESSION_CREATED_V1,
            ),
            s.CreateIndex(
                table="outbound_messages",
                index=definitions.IDX_OUTBOUND_STATE_CREATED_V1,
            ),
            s.CreateTrigger(
                name="set_outbound_messages_agent_identity",
                table="outbound_messages",
                event="insert",
                when=s.AnyOf(
                    conditions=(
                        s.IsNull(expression=s.Column(name="agent_id", table="new")),
                        s.IsNull(expression=s.Column(name="agent_name", table="new")),
                    )
                ),
                action=s.Update(
                    table="outbound_messages",
                    values={
                        "agent_id": s.Call(
                            name="coalesce",
                            arguments=(
                                s.Column(name="agent_id", table="new"),
                                s.Value(value=owner),
                            ),
                        ),
                        "agent_name": s.Call(
                            name="coalesce",
                            arguments=(
                                s.Column(name="agent_name", table="new"),
                                s.Value(value="default"),
                            ),
                        ),
                    },
                    where=s.Compare(
                        left=s.Column(name="outbound_message_id"),
                        operator="=",
                        right=s.Column(name="outbound_message_id", table="new"),
                    ),
                ),
            ),
        )
    )


async def unify_messages(session: s.Session) -> None:
    await transforms.order_messages(session)
    await session.apply_schema(
        (
            s.CreateIndex(
                table="_message_order_v18",
                index=s.Index(
                    name="_idx_message_order_id_v18",
                    columns=("message_id",),
                    unique=True,
                ),
            ),
            s.CreateIndex(
                table="_message_order_v18",
                index=s.Index(
                    name="_idx_message_order_inbound_seq_v18",
                    columns=("inbound_seq",),
                    unique=True,
                    where=s.Compare(
                        left=s.Column(name="direction"),
                        operator="=",
                        right=s.Value(value="inbound"),
                    ),
                ),
            ),
        )
    )
    await transforms.remap_snapshots(session)
    await session.apply_schema((s.CreateTable(table=definitions.MESSAGES_V18),))
    await session.copy(
        s.Copy(
            table="_messages_v18",
            query=s.Select(
                table="inbound_messages",
                columns={
                    "message_id": s.Column(name="message_id", table="inbound"),
                    "seq": s.Column(name="seq", table="message_order"),
                    "direction": s.Value(value="inbound"),
                    "agent_id": s.Column(name="agent_id", table="inbound"),
                    "session_id": s.Column(name="session_id", table="inbound"),
                    "channel_session_id": s.Column(
                        name="channel_session_id", table="inbound"
                    ),
                    "channel": s.Column(name="channel", table="inbound"),
                    "provider_thread_id": s.Column(
                        name="provider_thread_id", table="inbound"
                    ),
                    "provider_message_id": s.Column(
                        name="provider_message_id", table="inbound"
                    ),
                    "provider_time_ms": s.Column(
                        name="provider_time_ms", table="inbound"
                    ),
                    "received_at_ms": s.Column(name="received_at_ms", table="inbound"),
                    "sender": s.Column(name="sender", table="inbound"),
                    "message_type": s.Column(name="message_type", table="inbound"),
                    "target": s.Column(name="canonical_target", table="inbound"),
                    "target_kind": s.Column(name="target_kind", table="inbound"),
                    "reply_to_message_id": s.Column(
                        name="reply_to_message_id", table="inbound"
                    ),
                    "body": s.Column(name="body", table="inbound"),
                    "mentions_agent": s.Column(name="mentions_agent", table="inbound"),
                    "notifies_runtime": s.Column(
                        name="notifies_runtime", table="inbound"
                    ),
                    "provider_payload_ref": s.Column(
                        name="provider_payload_ref", table="inbound"
                    ),
                    "metadata_json": s.Column(name="metadata_json", table="inbound"),
                },
                alias="inbound",
                joins=(
                    s.Join(
                        table="_message_order_v18",
                        alias="message_order",
                        on=s.All(
                            conditions=(
                                s.Compare(
                                    left=s.Column(
                                        name="direction", table="message_order"
                                    ),
                                    operator="=",
                                    right=s.Value(value="inbound"),
                                ),
                                s.Compare(
                                    left=s.Column(
                                        name="message_id", table="message_order"
                                    ),
                                    operator="=",
                                    right=s.Column(name="message_id", table="inbound"),
                                ),
                            )
                        ),
                    ),
                ),
            ),
        )
    )
    await session.copy(
        s.Copy(
            table="_messages_v18",
            query=s.Select(
                table="outbound_messages",
                columns={
                    "message_id": s.Column(
                        name="outbound_message_id", table="outbound"
                    ),
                    "seq": s.Column(name="seq", table="message_order"),
                    "direction": s.Value(value="outbound"),
                    "agent_id": s.Column(name="agent_id", table="outbound"),
                    "session_id": s.Column(name="session_id", table="outbound"),
                    "channel_session_id": s.Column(
                        name="channel_session_id", table="outbound"
                    ),
                    "channel": s.Column(name="channel", table="channel_session"),
                    "provider_thread_id": s.Column(
                        name="provider_thread_id", table="channel_session"
                    ),
                    "provider_message_id": s.Column(
                        name="provider_message_id", table="outbound"
                    ),
                    "sender": s.Column(name="agent_name", table="outbound"),
                    "message_type": s.Value(value="text"),
                    "target": s.Column(name="target", table="outbound"),
                    "target_kind": s.Column(
                        name="target_kind", table="channel_session"
                    ),
                    "reply_to_message_id": s.Column(
                        name="reply_to_message_id", table="outbound"
                    ),
                    "body": s.Column(name="body", table="outbound"),
                    "command_id": s.Column(name="command_id", table="outbound"),
                    "delivery_state": s.Column(name="state", table="outbound"),
                    "snapshot_seq": s.Column(name="snapshot_seq", table="outbound"),
                    "current_inbound_seq": s.Column(
                        name="current_inbound_seq", table="outbound"
                    ),
                    "provider_receipt_ref": s.Column(
                        name="provider_receipt_ref", table="outbound"
                    ),
                    "created_at_ms": s.Column(name="created_at_ms", table="outbound"),
                    "provider_attempted_at_ms": s.Column(
                        name="provider_attempted_at_ms", table="outbound"
                    ),
                    "completed_at_ms": s.Column(
                        name="completed_at_ms", table="outbound"
                    ),
                    "error_kind": s.Column(name="error_kind", table="outbound"),
                    "error_message": s.Column(name="error_message", table="outbound"),
                    "metadata_json": s.Column(name="metadata_json", table="outbound"),
                    "attachments_json": s.Column(
                        name="attachments_json", table="outbound"
                    ),
                },
                alias="outbound",
                joins=(
                    s.Join(
                        table="_message_order_v18",
                        alias="message_order",
                        on=s.All(
                            conditions=(
                                s.Compare(
                                    left=s.Column(
                                        name="direction", table="message_order"
                                    ),
                                    operator="=",
                                    right=s.Value(value="outbound"),
                                ),
                                s.Compare(
                                    left=s.Column(
                                        name="message_id", table="message_order"
                                    ),
                                    operator="=",
                                    right=s.Column(
                                        name="outbound_message_id", table="outbound"
                                    ),
                                ),
                            )
                        ),
                    ),
                    s.Join(
                        table="channel_sessions",
                        alias="channel_session",
                        on=s.All(
                            conditions=(
                                s.Compare(
                                    left=s.Column(
                                        name="agent_id", table="channel_session"
                                    ),
                                    operator="=",
                                    right=s.Column(name="agent_id", table="outbound"),
                                ),
                                s.Compare(
                                    left=s.Column(name="id", table="channel_session"),
                                    operator="=",
                                    right=s.Column(
                                        name="channel_session_id", table="outbound"
                                    ),
                                ),
                            )
                        ),
                    ),
                ),
            ),
        )
    )
    await transforms.verify_unified_messages(session)
    await session.apply_schema(
        (
            s.DropTable(table="inbound_messages"),
            s.DropTable(table="outbound_messages"),
            s.RenameTable(table="_messages_v18", name="messages"),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_AGENT_SESSION_TARGET_SEQ_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_AGENT_DIRECTION_SEQ_V18
            ),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_INBOUND_PROVIDER_IDENTITY_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_OUTBOUND_COMMAND_V18
            ),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_OUTBOUND_STATE_CREATED_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_REPLY_TO_MESSAGE_V18
            ),
            s.DropTable(table="_message_order_v18"),
        )
    )


async def reminder_system_messages(session: s.Session) -> None:
    await session.apply_schema(
        (
            s.DropIndex(name="idx_messages_agent_session_target_seq", table="messages"),
            s.DropIndex(name="idx_messages_agent_direction_seq", table="messages"),
            s.DropIndex(
                name="idx_messages_inbound_provider_identity", table="messages"
            ),
            s.DropIndex(name="idx_messages_outbound_command", table="messages"),
            s.DropIndex(name="idx_messages_outbound_state_created", table="messages"),
            s.DropIndex(name="idx_messages_reply_to_message", table="messages"),
            s.RenameTable(table="messages", name="_messages_v19"),
            s.CreateTable(table=definitions.MESSAGES_V19),
        )
    )
    await session.copy(
        s.Copy(
            table="messages",
            query=s.Select(
                table="_messages_v19",
                columns={
                    "message_id": s.Column(name="message_id"),
                    "seq": s.Column(name="seq"),
                    "direction": s.Column(name="direction"),
                    "agent_id": s.Column(name="agent_id"),
                    "session_id": s.Column(name="session_id"),
                    "channel_session_id": s.Column(name="channel_session_id"),
                    "channel": s.Column(name="channel"),
                    "provider_thread_id": s.Column(name="provider_thread_id"),
                    "provider_message_id": s.Column(name="provider_message_id"),
                    "provider_time_ms": s.Column(name="provider_time_ms"),
                    "received_at_ms": s.Column(name="received_at_ms"),
                    "sender": s.Column(name="sender"),
                    "message_type": s.Column(name="message_type"),
                    "target": s.Column(name="target"),
                    "target_kind": s.Column(name="target_kind"),
                    "reply_to_message_id": s.Column(name="reply_to_message_id"),
                    "body": s.Column(name="body"),
                    "mentions_agent": s.Column(name="mentions_agent"),
                    "notifies_runtime": s.Column(name="notifies_runtime"),
                    "provider_payload_ref": s.Column(name="provider_payload_ref"),
                    "command_id": s.Column(name="command_id"),
                    "delivery_state": s.Column(name="delivery_state"),
                    "snapshot_seq": s.Column(name="snapshot_seq"),
                    "current_inbound_seq": s.Column(name="current_inbound_seq"),
                    "provider_receipt_ref": s.Column(name="provider_receipt_ref"),
                    "created_at_ms": s.Column(name="created_at_ms"),
                    "provider_attempted_at_ms": s.Column(
                        name="provider_attempted_at_ms"
                    ),
                    "completed_at_ms": s.Column(name="completed_at_ms"),
                    "error_kind": s.Column(name="error_kind"),
                    "error_message": s.Column(name="error_message"),
                    "metadata_json": s.Column(name="metadata_json"),
                    "attachments_json": s.Column(name="attachments_json"),
                },
                order_by=(s.Sort(expression=s.Column(name="seq"), nulls="first"),),
            ),
        )
    )
    await session.apply_schema(
        (
            s.DropTable(table="_messages_v19"),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_AGENT_SESSION_TARGET_SEQ_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_AGENT_DIRECTION_SEQ_V18
            ),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_INBOUND_PROVIDER_IDENTITY_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_OUTBOUND_COMMAND_V18
            ),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_OUTBOUND_STATE_CREATED_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_REPLY_TO_MESSAGE_V18
            ),
        )
    )
    await transforms.reminder_messages(session)
    await session.apply_schema(
        (
            s.DropIndex(
                name="idx_reminder_occurrences_owner_read_fired",
                table="reminder_occurrences",
            ),
            s.DropIndex(
                name="idx_reminder_occurrences_reminder_number",
                table="reminder_occurrences",
            ),
            s.DropTable(table="reminder_occurrences"),
        )
    )


async def remove_freshness_persistence(session: s.Session) -> None:
    await session.apply_schema(
        (
            s.DropIndex(name="idx_messages_agent_session_target_seq", table="messages"),
            s.DropIndex(name="idx_messages_agent_direction_seq", table="messages"),
            s.DropIndex(
                name="idx_messages_inbound_provider_identity", table="messages"
            ),
            s.DropIndex(name="idx_messages_outbound_command", table="messages"),
            s.DropIndex(name="idx_messages_outbound_state_created", table="messages"),
            s.DropIndex(name="idx_messages_reply_to_message", table="messages"),
            s.RenameTable(table="messages", name="_messages_v20"),
            s.CreateTable(table=definitions.MESSAGES_V20),
        )
    )
    await session.copy(
        s.Copy(
            table="messages",
            query=s.Select(
                table="_messages_v20",
                columns={
                    "message_id": s.Column(name="message_id"),
                    "seq": s.Column(name="seq"),
                    "direction": s.Column(name="direction"),
                    "agent_id": s.Column(name="agent_id"),
                    "session_id": s.Column(name="session_id"),
                    "channel_session_id": s.Column(name="channel_session_id"),
                    "channel": s.Column(name="channel"),
                    "provider_thread_id": s.Column(name="provider_thread_id"),
                    "provider_message_id": s.Column(name="provider_message_id"),
                    "provider_time_ms": s.Column(name="provider_time_ms"),
                    "received_at_ms": s.Column(name="received_at_ms"),
                    "sender": s.Column(name="sender"),
                    "message_type": s.Column(name="message_type"),
                    "target": s.Column(name="target"),
                    "target_kind": s.Column(name="target_kind"),
                    "reply_to_message_id": s.Column(name="reply_to_message_id"),
                    "body": s.Column(name="body"),
                    "mentions_agent": s.Column(name="mentions_agent"),
                    "notifies_runtime": s.Column(name="notifies_runtime"),
                    "provider_payload_ref": s.Column(name="provider_payload_ref"),
                    "command_id": s.Column(name="command_id"),
                    "delivery_state": s.Column(name="delivery_state"),
                    "provider_receipt_ref": s.Column(name="provider_receipt_ref"),
                    "created_at_ms": s.Column(name="created_at_ms"),
                    "provider_attempted_at_ms": s.Column(
                        name="provider_attempted_at_ms"
                    ),
                    "completed_at_ms": s.Column(name="completed_at_ms"),
                    "error_kind": s.Column(name="error_kind"),
                    "error_message": s.Column(name="error_message"),
                    "metadata_json": s.Column(name="metadata_json"),
                    "attachments_json": s.Column(name="attachments_json"),
                },
                order_by=(s.Sort(expression=s.Column(name="seq"), nulls="first"),),
            ),
        )
    )
    await session.apply_schema(
        (
            s.DropTable(table="_messages_v20"),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_AGENT_SESSION_TARGET_SEQ_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_AGENT_DIRECTION_SEQ_V18
            ),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_INBOUND_PROVIDER_IDENTITY_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_OUTBOUND_COMMAND_V18
            ),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_OUTBOUND_STATE_CREATED_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_REPLY_TO_MESSAGE_V18
            ),
            s.RenameTable(table="consumer_cursors", name="_consumer_cursors_v20"),
            s.CreateTable(table=definitions.CONSUMER_CURSORS_V20),
        )
    )
    await session.copy(
        s.Copy(
            table="consumer_cursors",
            query=s.Select(
                table="_consumer_cursors_v20",
                columns={
                    "session_id": s.Column(name="session_id"),
                    "delivered_through_seq": s.Column(name="delivered_through_seq"),
                    "last_check_at_ms": s.Column(name="last_check_at_ms"),
                    "last_read_at_ms": s.Column(name="last_read_at_ms"),
                    "updated_at_ms": s.Column(name="updated_at_ms"),
                },
            ),
        )
    )
    await session.apply_schema((s.DropTable(table="_consumer_cursors_v20"),))


async def remove_command_id(session: s.Session) -> None:
    await session.apply_schema(
        (
            s.DropIndex(name="idx_messages_agent_direction_seq", table="messages"),
            s.DropIndex(
                name="idx_messages_inbound_provider_identity", table="messages"
            ),
            s.DropIndex(name="idx_messages_outbound_command", table="messages"),
            s.DropIndex(name="idx_messages_outbound_state_created", table="messages"),
            s.DropIndex(name="idx_messages_reply_to_message", table="messages"),
            s.DropIndex(name="idx_messages_agent_thread_target_seq", table="messages"),
            s.RenameTable(table="messages", name="_messages_v30"),
            s.CreateTable(table=definitions.MESSAGES_V30),
        )
    )
    await session.copy(
        s.Copy(
            table="messages",
            query=s.Select(
                table="_messages_v30",
                columns={
                    "message_id": s.Column(name="message_id"),
                    "seq": s.Column(name="seq"),
                    "direction": s.Column(name="direction"),
                    "agent_id": s.Column(name="agent_id"),
                    "thread_id": s.Column(name="thread_id"),
                    "channel_session_id": s.Column(name="channel_session_id"),
                    "channel": s.Column(name="channel"),
                    "provider_thread_id": s.Column(name="provider_thread_id"),
                    "provider_message_id": s.Column(name="provider_message_id"),
                    "provider_time_ms": s.Column(name="provider_time_ms"),
                    "received_at_ms": s.Column(name="received_at_ms"),
                    "sender": s.Column(name="sender"),
                    "message_type": s.Column(name="message_type"),
                    "target": s.Column(name="target"),
                    "target_kind": s.Column(name="target_kind"),
                    "reply_to_message_id": s.Column(name="reply_to_message_id"),
                    "body": s.Column(name="body"),
                    "mentions_agent": s.Column(name="mentions_agent"),
                    "notifies_runtime": s.Column(name="notifies_runtime"),
                    "provider_payload_ref": s.Column(name="provider_payload_ref"),
                    "delivery_state": s.Column(name="delivery_state"),
                    "provider_receipt_ref": s.Column(name="provider_receipt_ref"),
                    "created_at_ms": s.Column(name="created_at_ms"),
                    "provider_attempted_at_ms": s.Column(
                        name="provider_attempted_at_ms"
                    ),
                    "completed_at_ms": s.Column(name="completed_at_ms"),
                    "error_kind": s.Column(name="error_kind"),
                    "error_message": s.Column(name="error_message"),
                    "metadata_json": s.Column(name="metadata_json"),
                    "attachments_json": s.Column(name="attachments_json"),
                    "sender_id": s.Column(name="sender_id"),
                    "sender_display_name": s.Column(name="sender_display_name"),
                },
                order_by=(s.Sort(expression=s.Column(name="seq"), nulls="first"),),
            ),
        )
    )
    await session.apply_schema(
        (
            s.DropTable(table="_messages_v30"),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_AGENT_DIRECTION_SEQ_V18
            ),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_INBOUND_PROVIDER_IDENTITY_V18,
            ),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_OUTBOUND_STATE_CREATED_V18,
            ),
            s.CreateIndex(
                table="messages", index=definitions.IDX_MESSAGES_REPLY_TO_MESSAGE_V18
            ),
            s.CreateIndex(
                table="messages",
                index=definitions.IDX_MESSAGES_AGENT_THREAD_TARGET_SEQ_V25,
            ),
        )
    )


async def channel_session_review(session: s.Session) -> None:
    await session.apply_schema(
        (
            s.AddColumn(
                table="channel_sessions",
                column=s.ColumnDefinition(
                    name="review",
                    type="text",
                    default="pending",
                    check=s.In(
                        expression=s.Column(name="review"),
                        values=("pending", "approved", "denied"),
                    ),
                ),
            ),
        )
    )
    await session.update(
        s.Update(table="channel_sessions", values={"review": s.Value(value="approved")})
    )
    await session.apply_schema((s.CreateTable(table=definitions.SETTINGS_V31),))


MODULE = ModuleSchema(
    tables=(
        "_consumer_cursors_v20",
        "_message_events_v18",
        "_message_order_v18",
        "_messages_v18",
        "_messages_v19",
        "_messages_v20",
        "_messages_v30",
        "_outbound_messages_provider_attempts",
        "bcn_sessions",
        "channel_sessions",
        "consumer_cursors",
        "handoffs",
        "inbound_attachments",
        "inbound_messages",
        "message_search",
        "messages",
        "node_state",
        "outbound_messages",
        "reminder_occurrences",
        "reminders",
        "runtime_attempts",
        "runtime_events",
        "runtime_sessions",
        "runtime_turns",
        "schema_migrations",
        "settings",
        "threads",
    ),
    progress=Progress(
        table="schema_migrations",
        identity="initial_node_schema",
        package="bazaar-contrib-node-migrations",
    ),
    migrations=(
        Migration(
            version=1,
            name="initial_node_schema",
            operations=(
                s.CreateTable(table=definitions.NODE_STATE_V1),
                s.CreateTable(table=definitions.CHANNEL_SESSIONS_V1),
                s.CreateTable(table=definitions.BCN_SESSIONS_V1),
                s.CreateTable(table=definitions.RUNTIME_SESSIONS_V1),
                s.CreateTable(table=definitions.RUNTIME_TURNS_V1),
                s.CreateTable(table=definitions.INBOUND_MESSAGES_V1),
                s.CreateTable(table=definitions.INBOUND_ATTACHMENTS_V1),
                s.CreateTable(table=definitions.OUTBOUND_MESSAGES_V1),
                s.CreateTable(table=definitions.CONSUMER_CURSORS_V1),
                s.CreateTable(table=definitions.RUNTIME_EVENTS_V1),
                s.CreateIndex(
                    table="inbound_messages",
                    index=s.Index(
                        name="idx_inbound_session_seq", columns=("session_id", "seq")
                    ),
                ),
                s.CreateIndex(
                    table="inbound_messages",
                    index=s.Index(name="idx_inbound_seq", columns=("seq",)),
                ),
                s.CreateIndex(
                    table="inbound_messages",
                    index=s.Index(
                        name="idx_inbound_channel_received",
                        columns=("channel_session_id", "received_at_ms"),
                    ),
                ),
                s.CreateIndex(
                    table="outbound_messages",
                    index=definitions.IDX_OUTBOUND_SESSION_CREATED_V1,
                ),
                s.CreateIndex(
                    table="outbound_messages",
                    index=definitions.IDX_OUTBOUND_STATE_CREATED_V1,
                ),
                s.CreateIndex(
                    table="runtime_turns",
                    index=s.Index(
                        name="idx_runtime_turns_session_state",
                        columns=("session_id", "state", "started_at_ms"),
                    ),
                ),
                s.CreateIndex(
                    table="runtime_events",
                    index=s.Index(
                        name="idx_runtime_events_session_seq",
                        columns=("bcn_session_id", "event_seq"),
                    ),
                ),
                s.CreateIndex(
                    table="runtime_events",
                    index=s.Index(
                        name="idx_runtime_events_name_seq",
                        columns=("event_name", "event_seq"),
                    ),
                ),
                s.CreateIndex(
                    table="runtime_events",
                    index=s.Index(
                        name="idx_runtime_events_created", columns=("created_at_ms",)
                    ),
                ),
            ),
        ),
        Migration(
            version=2,
            name="session_mapping_indexes",
            operations=(
                s.CreateIndex(
                    table="channel_sessions",
                    index=s.Index(
                        name="idx_channel_sessions_provider_identity",
                        columns=("channel", "provider_thread_id"),
                    ),
                ),
                s.CreateIndex(
                    table="bcn_sessions",
                    index=s.Index(
                        name="idx_bcn_sessions_channel", columns=("channel_session_id",)
                    ),
                ),
                s.CreateIndex(
                    table="runtime_sessions",
                    index=s.Index(
                        name="idx_runtime_sessions_bcn", columns=("bcn_session_id",)
                    ),
                ),
            ),
        ),
        Migration(
            version=3,
            name="message_log_indexes",
            operations=(
                s.CreateIndex(
                    table="inbound_messages",
                    index=s.Index(
                        name="idx_inbound_provider_identity",
                        columns=("channel", "provider_message_id"),
                    ),
                ),
                s.CreateIndex(
                    table="inbound_messages",
                    index=s.Index(
                        name="idx_inbound_session_target_seq",
                        columns=("session_id", "canonical_target", "seq"),
                    ),
                ),
            ),
        ),
        Migration(version=4, name="runtime_attempt_facts", apply=runtime_attempt_facts),
        Migration(
            version=5,
            name="inbound_message_references",
            apply=inbound_message_references,
        ),
        Migration(
            version=6,
            name="inbound_message_reference_integrity",
            apply=transforms.clear_invalid_references,
        ),
        Migration(
            version=7,
            name="inbound_provider_identity",
            operations=(
                s.DropIndex(
                    name="idx_inbound_provider_identity", table="inbound_messages"
                ),
                s.CreateIndex(
                    table="inbound_messages",
                    index=s.Index(
                        name="idx_inbound_provider_identity",
                        columns=(
                            "channel",
                            "provider_thread_id",
                            "provider_message_id",
                        ),
                        unique=True,
                    ),
                ),
            ),
        ),
        Migration(
            version=8,
            name="transient_stream_events",
            apply=transforms.remove_transient_events,
        ),
        Migration(
            version=9,
            name="remove_runtime_events",
            operations=(
                s.AddColumn(
                    table="schema_migrations",
                    column=s.ColumnDefinition(
                        name="compaction_completed_at_ms", type="integer", nullable=True
                    ),
                ),
                s.DropTable(table="runtime_events"),
            ),
        ),
        Migration(
            version=10,
            name="add_outbound_attachments",
            operations=(
                s.AddColumn(
                    table="outbound_messages",
                    column=s.ColumnDefinition(
                        name="attachments_json", type="text", default="[]"
                    ),
                ),
            ),
        ),
        Migration(
            version=11,
            name="remove_runtime_session_mapping",
            operations=(
                s.DropIndex(name="idx_runtime_sessions_bcn", table="runtime_sessions"),
                s.DropTable(table="runtime_sessions"),
            ),
        ),
        Migration(
            version=12,
            name="add_reminders",
            operations=(
                s.CreateTable(table=definitions.REMINDERS_V12),
                s.CreateTable(table=definitions.REMINDER_OCCURRENCES_V12),
                s.CreateIndex(
                    table="reminders", index=definitions.IDX_REMINDERS_STATE_NEXT_V12
                ),
                s.CreateIndex(
                    table="reminders",
                    index=s.Index(
                        name="idx_reminders_owner_state_updated",
                        columns=(
                            "owner_session_id",
                            "state",
                            "updated_at_ms",
                            "reminder_id",
                        ),
                    ),
                ),
                s.CreateIndex(
                    table="reminder_occurrences",
                    index=s.Index(
                        name="idx_reminder_occurrences_owner_read_fired",
                        columns=(
                            "owner_session_id",
                            "read_at_ms",
                            "fired_at_ms",
                            "occurrence_id",
                        ),
                    ),
                ),
                s.CreateIndex(
                    table="reminder_occurrences",
                    index=s.Index(
                        name="idx_reminder_occurrences_reminder_number",
                        columns=("reminder_id", "occurrence_no"),
                    ),
                ),
            ),
        ),
        Migration(version=13, name="add_agent_ownership", apply=add_agent_ownership),
        Migration(
            version=14,
            name="add_inbox_discovery_indexes",
            operations=(
                s.CreateIndex(
                    table="inbound_messages",
                    index=s.Index(
                        name="idx_inbound_agent_session_seq",
                        order_by=(
                            s.Sort(expression=s.Column(name="agent_id"), nulls="first"),
                            s.Sort(
                                expression=s.Column(name="session_id"), nulls="first"
                            ),
                            s.Sort(expression=s.Column(name="seq"), descending=True),
                        ),
                    ),
                ),
                s.CreateIndex(
                    table="inbound_messages",
                    index=s.Index(
                        name="idx_inbound_agent_target_session",
                        columns=("agent_id", "canonical_target", "session_id"),
                    ),
                ),
            ),
        ),
        Migration(
            version=15,
            name="add_handoffs",
            operations=(
                s.CreateTable(table=definitions.HANDOFFS_V15),
                s.CreateIndex(
                    table="handoffs",
                    index=s.Index(
                        name="idx_handoffs_agent_target_read_seq",
                        columns=("agent_id", "target_session_id", "read_at_ms", "seq"),
                    ),
                ),
            ),
        ),
        Migration(
            version=16, name="remove_outbound_drafts", apply=remove_outbound_drafts
        ),
        Migration(
            version=17,
            name="remove_agent_identity_triggers",
            operations=(
                s.DropTrigger(
                    name="set_channel_sessions_agent_id", table="channel_sessions"
                ),
                s.DropTrigger(name="set_bcn_sessions_agent_id", table="bcn_sessions"),
                s.DropTrigger(
                    name="set_inbound_messages_agent_id", table="inbound_messages"
                ),
                s.DropTrigger(
                    name="set_outbound_messages_agent_identity",
                    table="outbound_messages",
                ),
                s.DropTrigger(
                    name="set_runtime_attempts_agent_id", table="runtime_attempts"
                ),
                s.DropTrigger(name="set_reminders_agent_id", table="reminders"),
                s.DropTrigger(
                    name="set_reminder_occurrences_agent_id",
                    table="reminder_occurrences",
                ),
            ),
        ),
        Migration(version=18, name="unify_messages", apply=unify_messages),
        Migration(
            version=19, name="reminder_system_messages", apply=reminder_system_messages
        ),
        Migration(
            version=20,
            name="remove_freshness_persistence",
            apply=remove_freshness_persistence,
        ),
        Migration(
            version=21,
            name="remove_handoffs",
            operations=(
                s.DropIndex(
                    name="idx_handoffs_agent_target_read_seq", table="handoffs"
                ),
                s.DropTable(table="handoffs"),
            ),
        ),
        Migration(
            version=22,
            name="readable_targets",
            operations=(
                s.AddColumn(
                    table="channel_sessions",
                    column=s.ColumnDefinition(
                        name="target_display_name", type="text", nullable=True
                    ),
                ),
                s.AddColumn(
                    table="channel_sessions",
                    column=s.ColumnDefinition(
                        name="target_handle", type="text", nullable=True
                    ),
                ),
                s.AddColumn(
                    table="channel_sessions",
                    column=s.ColumnDefinition(
                        name="target_handle_key", type="text", nullable=True
                    ),
                ),
                s.CreateIndex(
                    table="channel_sessions",
                    index=definitions.IDX_CHANNEL_SESSIONS_TARGET_HANDLE_V22,
                ),
            ),
        ),
        Migration(
            version=23,
            name="persist_sender_identity",
            operations=(
                s.AddColumn(
                    table="messages",
                    column=s.ColumnDefinition(
                        name="sender_id", type="text", nullable=True
                    ),
                ),
            ),
        ),
        Migration(
            version=24,
            name="persist_sender_display_name",
            operations=(
                s.AddColumn(
                    table="messages",
                    column=s.ColumnDefinition(
                        name="sender_display_name", type="text", nullable=True
                    ),
                ),
            ),
        ),
        Migration(
            version=25,
            name="rename_sessions_to_threads",
            operations=(
                s.DropIndex(name="idx_bcn_sessions_channel", table="bcn_sessions"),
                s.DropIndex(
                    name="idx_messages_agent_session_target_seq", table="messages"
                ),
                s.DropIndex(
                    name="idx_reminders_owner_state_updated", table="reminders"
                ),
                s.RenameTable(table="bcn_sessions", name="threads"),
                s.RenameColumn(table="messages", column="session_id", name="thread_id"),
                s.RenameColumn(
                    table="consumer_cursors", column="session_id", name="thread_id"
                ),
                s.RenameColumn(
                    table="reminders", column="owner_session_id", name="owner_thread_id"
                ),
                s.CreateIndex(
                    table="threads", index=definitions.IDX_THREADS_CHANNEL_V25
                ),
                s.CreateIndex(
                    table="messages",
                    index=definitions.IDX_MESSAGES_AGENT_THREAD_TARGET_SEQ_V25,
                ),
                s.CreateIndex(
                    table="reminders",
                    index=definitions.IDX_REMINDERS_OWNER_STATE_UPDATED_V25,
                ),
            ),
        ),
        Migration(
            version=26,
            name="remove_handoff_messages",
            apply=transforms.remove_handoff_messages,
        ),
        Migration(
            version=27, name="name_dms_by_the_peer_id", apply=transforms.merge_peer_dms
        ),
        Migration(
            version=28,
            name="channel_session_identity",
            operations=(
                s.AddColumn(
                    table="channel_sessions",
                    column=s.ColumnDefinition(
                        name="channel_identity", type="text", nullable=True
                    ),
                ),
            ),
        ),
        Migration(
            version=29,
            name="backfill_channel_identity",
            apply=transforms.backfill_channel_identity,
        ),
        Migration(version=30, name="remove_command_id", apply=remove_command_id),
        Migration(
            version=31, name="channel_session_review", apply=channel_session_review
        ),
        Migration(
            version=32,
            name="message_search",
            operations=(
                s.CreateIndex(table="messages", index=definitions.MESSAGE_SEARCH_V32),
                s.CreateIndex(
                    table="messages",
                    index=definitions.IDX_MESSAGES_AGENT_SEARCH_TIME_V32,
                ),
            ),
        ),
    ),
)
