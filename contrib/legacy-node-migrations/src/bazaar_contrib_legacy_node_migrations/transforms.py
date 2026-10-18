"""Historical Node data transformations using the public storage session."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast
lazy import json

import bazaar_contrib_storage as s


def equals(name: str, value: s.Scalar, table: str | None = None) -> s.Compare:
    return s.Compare(
        left=s.Column(name=name, table=table), operator="=", right=s.Value(value=value)
    )


async def records(
    session: s.Session, table: str, key: str, where: s.Predicate | None = None
) -> AsyncIterator[s.Record]:
    last: s.Scalar = None
    while True:
        conditions = (where,) if where is not None else ()
        if last is not None:
            conditions += (
                s.Compare(
                    left=s.Column(name=key), operator=">", right=s.Value(value=last)
                ),
            )
        rows = await session.select(
            s.Select(
                table=table,
                where=s.All(conditions=conditions),
                order_by=(s.Sort(expression=s.Column(name=key)),),
                limit=256,
            )
        )
        if not rows:
            return
        for row in rows:
            yield row
        last = rows[-1][key]


async def resolve_references(session: s.Session) -> None:
    async for row in records(
        session,
        "inbound_messages",
        "seq",
        s.IsNull(expression=s.Column(name="reply_to_message_id"), exclude=True),
    ):
        referenced = await session.select(
            s.Select(
                table="inbound_messages",
                columns={"message_id": s.Column(name="message_id")},
                where=s.All(
                    conditions=(
                        equals("channel", row["channel"]),
                        equals("provider_message_id", row["reply_to_message_id"]),
                    )
                ),
                order_by=(s.Sort(expression=s.Column(name="seq")),),
                limit=1,
            )
        )
        await session.update(
            s.Update(
                table="inbound_messages",
                values={
                    "reply_to_message_id": s.Value(
                        value=referenced[0]["message_id"] if referenced else None
                    )
                },
                where=equals("seq", row["seq"]),
            )
        )


async def clear_invalid_references(session: s.Session) -> None:
    async for row in records(
        session,
        "inbound_messages",
        "seq",
        s.IsNull(expression=s.Column(name="reply_to_message_id"), exclude=True),
    ):
        referenced = await session.select(
            s.Select(
                table="inbound_messages",
                columns={"message_id": s.Column(name="message_id")},
                where=s.All(
                    conditions=(
                        equals("message_id", row["reply_to_message_id"]),
                        equals("session_id", row["session_id"]),
                        s.Compare(
                            left=s.Column(name="seq"),
                            operator="<",
                            right=s.Value(value=row["seq"]),
                        ),
                    )
                ),
                limit=1,
            )
        )
        if not referenced:
            await session.update(
                s.Update(
                    table="inbound_messages",
                    values={"reply_to_message_id": s.Value(value=None)},
                    where=equals("seq", row["seq"]),
                )
            )


async def remove_transient_events(session: s.Session) -> None:
    boundaries = {
        "item/started",
        "item/completed",
        "item/autoApprovalReview/started",
        "item/autoApprovalReview/completed",
    }
    async for row in records(
        session,
        "runtime_events",
        "event_seq",
        equals("event_name", "codex.turn.progress"),
    ):
        metadata = (
            json.loads(cast(str, row["metadata_json"]))
            if row["metadata_json"] is not None
            else {}
        )
        method = metadata.get("provider_method") if isinstance(metadata, dict) else None
        if method == "turn/progress" or (
            isinstance(method, str)
            and method.lower().startswith("item/")
            and method not in boundaries
        ):
            await session.delete(
                s.Delete(
                    table="runtime_events", where=equals("event_seq", row["event_seq"])
                )
            )


async def workspace_owner(session: s.Session) -> s.Scalar:
    rows = await session.select(
        s.Select(
            table="node_state",
            columns={"workspace_id": s.Column(name="workspace_id")},
            where=equals("singleton_key", 1),
            limit=1,
        )
    )
    owner = rows[0]["workspace_id"] if rows else None
    if not isinstance(owner, str) or not owner:
        for table in (
            "channel_sessions",
            "bcn_sessions",
            "inbound_messages",
            "outbound_messages",
            "runtime_attempts",
            "reminders",
            "reminder_occurrences",
        ):
            if await session.select(s.Select(table=table, limit=1)):
                raise s.MigrationError(
                    "existing Node data requires a workspace identity before adding agent ownership"
                )
    return owner


async def existing_owner(session: s.Session) -> s.Scalar:
    for table in (
        "channel_sessions",
        "bcn_sessions",
        "inbound_messages",
        "outbound_messages",
        "runtime_attempts",
        "reminders",
        "reminder_occurrences",
    ):
        rows = await session.select(
            s.Select(
                table=table,
                columns={"agent_id": s.Column(name="agent_id")},
                where=s.IsNull(expression=s.Column(name="agent_id"), exclude=True),
                limit=1,
            )
        )
        if rows:
            return rows[0]["agent_id"]
    return None


async def order_messages(session: s.Session) -> None:
    await session.apply_schema(
        (
            s.CreateTable(
                temporary=True,
                table=s.Table(
                    name="_message_events_v18",
                    columns=(
                        s.ColumnDefinition(name="message_id", type="text"),
                        s.ColumnDefinition(name="direction", type="text"),
                        s.ColumnDefinition(
                            name="inbound_seq", type="integer", nullable=True
                        ),
                        s.ColumnDefinition(
                            name="event_time_ms", type="integer", nullable=True
                        ),
                    ),
                    primary_key=("message_id",),
                ),
            ),
            s.CreateTable(
                temporary=True,
                table=s.Table(
                    name="_message_order_v18",
                    columns=(
                        s.ColumnDefinition(name="message_id", type="text"),
                        s.ColumnDefinition(name="direction", type="text"),
                        s.ColumnDefinition(
                            name="inbound_seq", type="integer", nullable=True
                        ),
                        s.ColumnDefinition(name="seq", type="integer", unique=True),
                    ),
                    primary_key=("message_id",),
                ),
            ),
        )
    )
    await session.copy(
        s.Copy(
            table="_message_events_v18",
            query=s.Select(
                table="inbound_messages",
                columns={
                    "message_id": s.Column(name="message_id"),
                    "direction": s.Value(value="inbound"),
                    "inbound_seq": s.Column(name="seq"),
                    "event_time_ms": s.Call(
                        name="coalesce",
                        arguments=(
                            s.Column(name="provider_time_ms"),
                            s.Column(name="received_at_ms"),
                        ),
                    ),
                },
            ),
        )
    )
    await session.copy(
        s.Copy(
            table="_message_events_v18",
            query=s.Select(
                table="outbound_messages",
                columns={
                    "message_id": s.Column(name="outbound_message_id"),
                    "direction": s.Value(value="outbound"),
                    "inbound_seq": s.Value(value=None),
                    "event_time_ms": s.Column(name="created_at_ms"),
                },
            ),
        )
    )
    await session.copy(
        s.Copy(
            table="_message_order_v18",
            query=s.Select(
                table="_message_events_v18",
                columns={
                    "message_id": s.Column(name="message_id"),
                    "direction": s.Column(name="direction"),
                    "inbound_seq": s.Column(name="inbound_seq"),
                    "seq": s.RowNumber(
                        order_by=(
                            s.Sort(
                                expression=s.Column(name="event_time_ms"), nulls="first"
                            ),
                            s.Sort(expression=s.Column(name="message_id")),
                        )
                    ),
                },
            ),
        )
    )
    await session.apply_schema((s.DropTable(table="_message_events_v18"),))


async def remap_snapshots(session: s.Session) -> None:
    for table, key, columns in (
        (
            "consumer_cursors",
            "session_id",
            ("delivered_through_seq", "inbox_snapshot_seq"),
        ),
        (
            "outbound_messages",
            "outbound_message_id",
            ("snapshot_seq", "current_inbound_seq"),
        ),
    ):
        async for row in records(session, table, key):
            values: dict[str, s.Expression] = {}
            for column in columns:
                value = row[column]
                if value is None or value == 0:
                    continue
                mapped = await session.select(
                    s.Select(
                        table="_message_order_v18",
                        columns={"seq": s.Column(name="seq")},
                        where=s.All(
                            conditions=(
                                equals("direction", "inbound"),
                                equals("inbound_seq", value),
                            )
                        ),
                        limit=1,
                    )
                )
                if not mapped:
                    raise s.MigrationError(
                        f"{table}.{column} references an absent inbound sequence {value}"
                    )
                values[column] = s.Value(value=mapped[0]["seq"])
            if values:
                await session.update(
                    s.Update(table=table, values=values, where=equals(key, row[key]))
                )


async def verify_unified_messages(session: s.Session) -> None:
    counts: dict[str, s.Scalar] = {}
    for table in ("_messages_v18", "inbound_messages", "outbound_messages"):
        rows = await session.select(
            s.Select(table=table, columns={"count": s.Call(name="count")})
        )
        counts[table] = rows[0]["count"]
    if counts["_messages_v18"] != cast(int, counts["inbound_messages"]) + cast(
        int, counts["outbound_messages"]
    ):
        raise s.MigrationError("message unification would lose historical messages")
    orphans = await session.select(
        s.Select(
            table="inbound_attachments",
            alias="attachment",
            columns={"message_id": s.Column(name="message_id", table="attachment")},
            joins=(
                s.Join(
                    table="_messages_v18",
                    alias="message",
                    kind="left",
                    on=s.All(
                        conditions=(
                            s.Compare(
                                left=s.Column(name="message_id", table="message"),
                                operator="=",
                                right=s.Column(name="message_id", table="attachment"),
                            ),
                            equals("direction", "inbound", "message"),
                        )
                    ),
                ),
            ),
            where=s.IsNull(expression=s.Column(name="message_id", table="message")),
            limit=1,
        )
    )
    if orphans:
        raise s.MigrationError(
            "message unification found an attachment without its inbound message"
        )


async def reminder_messages(session: s.Session) -> None:
    maximum = await session.select(
        s.Select(
            table="messages",
            columns={
                "seq": s.Call(
                    name="coalesce",
                    arguments=(
                        s.Call(name="max", arguments=(s.Column(name="seq"),)),
                        s.Value(value=0),
                    ),
                )
            },
        )
    )
    sequence = cast(int, maximum[0]["seq"])
    columns: dict[str, s.Expression] = {
        name: s.Column(name=name, table="occurrence")
        for name in (
            "occurrence_id",
            "agent_id",
            "owner_session_id",
            "reminder_id",
            "fired_at_ms",
            "next_fire_at_ms",
        )
    }
    columns.update(
        {
            name: s.Column(name=name, table="reminder")
            for name in ("title", "repeat_rule")
        }
    )
    columns.update(
        {
            name: s.Column(name=name, table="anchor")
            for name in (
                "channel_session_id",
                "channel",
                "provider_thread_id",
                "target",
                "target_kind",
            )
        }
    )
    rows = await session.select(
        s.Select(
            table="reminder_occurrences",
            alias="occurrence",
            columns=columns,
            where=s.IsNull(expression=s.Column(name="read_at_ms", table="occurrence")),
            joins=(
                s.Join(
                    table="reminders",
                    alias="reminder",
                    on=s.All(
                        conditions=tuple(
                            s.Compare(
                                left=s.Column(name=name, table="reminder"),
                                operator="=",
                                right=s.Column(name=name, table="occurrence"),
                            )
                            for name in ("agent_id", "reminder_id", "owner_session_id")
                        )
                    ),
                ),
                s.Join(
                    table="messages",
                    alias="anchor",
                    on=s.All(
                        conditions=(
                            s.Compare(
                                left=s.Column(name="agent_id", table="anchor"),
                                operator="=",
                                right=s.Column(name="agent_id", table="occurrence"),
                            ),
                            s.Compare(
                                left=s.Column(name="session_id", table="anchor"),
                                operator="=",
                                right=s.Column(
                                    name="owner_session_id", table="occurrence"
                                ),
                            ),
                            s.Compare(
                                left=s.Column(name="message_id", table="anchor"),
                                operator="=",
                                right=s.Column(
                                    name="anchor_message_id", table="occurrence"
                                ),
                            ),
                            equals("direction", "inbound", "anchor"),
                        )
                    ),
                ),
            ),
            order_by=(
                s.Sort(expression=s.Column(name="fired_at_ms", table="occurrence")),
                s.Sort(expression=s.Column(name="occurrence_id", table="occurrence")),
            ),
        )
    )
    for row in rows:
        sequence += 1
        reminder_id = cast(str, row["reminder_id"])
        title = json.dumps(row["title"], ensure_ascii=False, separators=(",", ":"))
        body = f"🔔 Reminder #{reminder_id[:8]} "
        if row["next_fire_at_ms"] is None:
            body += f"(one-time) — {row['target']} — {title}"
        else:
            next_fire = (
                datetime.fromtimestamp(cast(int, row["next_fire_at_ms"]) / 1000, UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            body += f"(recurring · {row['repeat_rule']}) — {row['target']} — {title}\nNext iteration: {next_fire}"
        await session.insert(
            s.Insert(
                table="messages",
                rows=(
                    {
                        "message_id": row["occurrence_id"],
                        "seq": sequence,
                        "direction": "inbound",
                        "agent_id": row["agent_id"],
                        "session_id": row["owner_session_id"],
                        "channel_session_id": row["channel_session_id"],
                        "channel": row["channel"],
                        "provider_thread_id": row["provider_thread_id"],
                        "provider_message_id": None,
                        "provider_time_ms": None,
                        "received_at_ms": row["fired_at_ms"],
                        "sender": "system",
                        "message_type": "text",
                        "target": row["target"],
                        "target_kind": row["target_kind"],
                        "reply_to_message_id": None,
                        "body": body,
                        "mentions_agent": 0,
                        "notifies_runtime": 1,
                        "provider_payload_ref": None,
                        "metadata_json": '{"sender_kind":"system","system_message_kind":"reminder"}',
                    },
                ),
            )
        )


async def remove_handoff_messages(session: s.Session) -> None:
    async for row in records(session, "messages", "message_id"):
        metadata = (
            json.loads(cast(str, row["metadata_json"]))
            if row["metadata_json"] is not None
            else {}
        )
        if (
            not isinstance(metadata, dict)
            or metadata.get("system_message_kind") != "handoff"
        ):
            continue
        await session.update(
            s.Update(
                table="messages",
                values={"reply_to_message_id": s.Value(value=None)},
                where=equals("reply_to_message_id", row["message_id"]),
            )
        )
        await session.delete(
            s.Delete(
                table="reminders", where=equals("anchor_message_id", row["message_id"])
            )
        )
        await session.delete(
            s.Delete(table="messages", where=equals("message_id", row["message_id"]))
        )
    async for row in records(session, "consumer_cursors", "thread_id"):
        maximum = await session.select(
            s.Select(
                table="messages",
                columns={
                    "seq": s.Call(
                        name="coalesce",
                        arguments=(
                            s.Call(name="max", arguments=(s.Column(name="seq"),)),
                            s.Value(value=0),
                        ),
                    )
                },
                where=s.All(
                    conditions=(
                        equals("thread_id", row["thread_id"]),
                        equals("direction", "inbound"),
                    )
                ),
            )
        )
        if cast(int, row["delivered_through_seq"]) > cast(int, maximum[0]["seq"]):
            await session.update(
                s.Update(
                    table="consumer_cursors",
                    values={"delivered_through_seq": s.Value(value=maximum[0]["seq"])},
                    where=equals("thread_id", row["thread_id"]),
                )
            )


async def merge_peer_dms(session: s.Session) -> None:
    merges: list[tuple[s.Record, s.Record, s.Record, s.Record]] = []
    renames: list[tuple[s.Scalar, str]] = []
    async for opened in records(
        session,
        "channel_sessions",
        "id",
        s.All(
            conditions=(
                equals("target_kind", "dm"),
                s.IsNull(expression=s.Column(name="target_handle"), exclude=True),
                s.IsNull(expression=s.Column(name="provider_thread_id"), exclude=True),
            )
        ),
    ):
        marker = f":@{opened['target_handle']}:"
        provider_thread = cast(str, opened["provider_thread_id"])
        if marker not in provider_thread:
            continue
        spoken = await session.select(
            s.Select(
                table="messages",
                columns={"sender_id": s.Column(name="sender_id")},
                where=s.All(
                    conditions=(
                        equals("agent_id", opened["agent_id"]),
                        equals("channel", opened["channel"]),
                        equals("direction", "inbound"),
                        s.IsNull(expression=s.Column(name="sender_id"), exclude=True),
                        s.Compare(
                            left=s.Call(
                                name="lower", arguments=(s.Column(name="sender"),)
                            ),
                            operator="=",
                            right=s.Value(value=opened["target_handle_key"]),
                        ),
                    )
                ),
                order_by=(s.Sort(expression=s.Column(name="seq"), descending=True),),
                limit=1,
            )
        )
        if not spoken:
            continue
        provider_thread = provider_thread.replace(marker, f":{spoken[0]['sender_id']}:")
        peers = await session.select(
            s.Select(
                table="channel_sessions",
                where=s.All(
                    conditions=(
                        equals("agent_id", opened["agent_id"]),
                        equals("channel", opened["channel"]),
                        equals("provider_thread_id", provider_thread),
                    )
                ),
                limit=1,
            )
        )
        opened_threads = await session.select(
            s.Select(
                table="threads",
                where=s.All(
                    conditions=(
                        equals("agent_id", opened["agent_id"]),
                        equals("channel_session_id", opened["id"]),
                    )
                ),
                limit=1,
            )
        )
        peer_threads = (
            await session.select(
                s.Select(
                    table="threads",
                    where=s.All(
                        conditions=(
                            equals("agent_id", opened["agent_id"]),
                            equals("channel_session_id", peers[0]["id"]),
                        )
                    ),
                    limit=1,
                )
            )
            if peers
            else ()
        )
        if peers and opened_threads and peer_threads:
            merges.append((opened, peers[0], opened_threads[0], peer_threads[0]))
        else:
            renames.append((opened["id"], provider_thread))
    groups: dict[s.Scalar, list[s.Scalar]] = {}
    for _, _, opened, peer in merges:
        groups.setdefault(peer["id"], []).append(opened["id"])
    for peer, opened in groups.items():
        threads = (peer, *opened)
        cursors = await session.select(
            s.Select(
                table="consumer_cursors",
                where=s.In(expression=s.Column(name="thread_id"), values=threads),
            )
        )
        current = next((row for row in cursors if row["thread_id"] == peer), None)
        if current is None:
            continue
        waiting = await session.select(
            s.Select(
                table="messages",
                alias="waiting",
                columns={
                    "seq": s.Call(
                        name="min", arguments=(s.Column(name="seq", table="waiting"),)
                    )
                },
                joins=(
                    s.Join(
                        table="consumer_cursors",
                        alias="reading",
                        on=s.Compare(
                            left=s.Column(name="thread_id", table="reading"),
                            operator="=",
                            right=s.Column(name="thread_id", table="waiting"),
                        ),
                    ),
                ),
                where=s.All(
                    conditions=(
                        s.In(
                            expression=s.Column(name="thread_id", table="waiting"),
                            values=threads,
                        ),
                        equals("direction", "inbound", "waiting"),
                        s.Compare(
                            left=s.Column(name="seq", table="waiting"),
                            operator=">",
                            right=s.Column(
                                name="delivered_through_seq", table="reading"
                            ),
                        ),
                    )
                ),
            )
        )
        delivered = (
            cast(int, waiting[0]["seq"]) - 1
            if waiting[0]["seq"] is not None
            else max(cast(int, row["delivered_through_seq"]) for row in cursors)
        )
        await session.update(
            s.Update(
                table="consumer_cursors",
                values={"delivered_through_seq": s.Value(value=delivered)},
                where=equals("thread_id", peer),
            )
        )
    for opened, peer, opened_thread, peer_thread in merges:
        await session.delete(
            s.Delete(
                table="consumer_cursors", where=equals("thread_id", opened_thread["id"])
            )
        )
        await session.update(
            s.Update(
                table="reminders",
                values={"owner_thread_id": s.Value(value=peer_thread["id"])},
                where=equals("owner_thread_id", opened_thread["id"]),
            )
        )
        await session.update(
            s.Update(
                table="messages",
                values={
                    "thread_id": s.Value(value=peer_thread["id"]),
                    "target": s.Value(value=f"dm:{peer['id']}"),
                    "channel_session_id": s.Value(value=peer["id"]),
                },
                where=equals("channel_session_id", opened["id"]),
            )
        )
        await session.delete(
            s.Delete(table="threads", where=equals("id", opened_thread["id"]))
        )
        await session.delete(
            s.Delete(table="channel_sessions", where=equals("id", opened["id"]))
        )
    for identity, provider_thread in renames:
        await session.update(
            s.Update(
                table="channel_sessions",
                values={"provider_thread_id": s.Value(value=provider_thread)},
                where=equals("id", identity),
            )
        )


async def backfill_channel_identity(session: s.Session) -> None:
    async for row in records(
        session,
        "channel_sessions",
        "id",
        s.All(
            conditions=(
                s.IsNull(expression=s.Column(name="channel_identity")),
                s.IsNull(expression=s.Column(name="provider_thread_id"), exclude=True),
            )
        ),
    ):
        provider_thread = cast(str, row["provider_thread_id"])
        if (
            row["channel"] == "telegram"
            and provider_thread.lower().startswith("telegram:")
        ) or (
            row["channel"] == "lark" and provider_thread.lower().startswith("lark:bot:")
        ):
            identity, separator, _ = provider_thread[9:].partition(":")
            if identity and separator:
                await session.update(
                    s.Update(
                        table="channel_sessions",
                        values={"channel_identity": s.Value(value=identity)},
                        where=equals("id", row["id"]),
                    )
                )
