from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid7

import aiosqlite
import pytest
import pytest_asyncio

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.contrib.sqlite.executor import (
    SqliteQueryTimeoutError,
    SqliteSession,
)
from bazaar_compute_node.contrib.sqlite.migrations import MIGRATIONS, apply_migrations
from bazaar_compute_node.contrib.sqlite.repository import SqliteRepository
from bazaar_compute_node.core.command import MessageSearchRequest
from bazaar_compute_node.core.models import (
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    Message,
    MessageDirection,
    OutboundDeliveryState,
    Review,
    SenderIdentity,
    SenderKind,
    Thread,
)
from bazaar_compute_node.core.storage import IStorageScope


async def _conversation(
    scope: IStorageScope, *, review: Review = Review.APPROVED
) -> tuple[ChannelSession, Thread]:
    channel = ChannelSession(
        id=str(uuid7()),
        channel="telegram",
        provider_thread_id=str(uuid7()),
        created_at_ms=1,
        updated_at_ms=1,
        target_kind=ChannelTargetKind.GROUP,
        target_display_name="发布讨论",
        following=False,
        review=review,
    )
    thread = Thread(
        id=str(uuid7()),
        channel_session_id=channel.id,
        workspace_id=scope.agent_id,
        created_at_ms=1,
        updated_at_ms=1,
    )
    await scope.save_channel_session(channel)
    await scope.save_thread(thread)
    return channel, thread


async def _message(
    scope: IStorageScope,
    conversation: tuple[ChannelSession, Thread],
    body: str,
    at_ms: int,
    *,
    sender: SenderIdentity | None = None,
    state: OutboundDeliveryState | None = None,
) -> Message:
    channel, thread = conversation
    return await scope.save_message(
        Message(
            direction=MessageDirection.OUTBOUND if state else MessageDirection.INBOUND,
            seq=0,
            message_id=str(uuid7()),
            thread_id=thread.id,
            channel_session_id=channel.id,
            channel=channel.channel,
            provider_thread_id=channel.provider_thread_id,
            provider_message_id=(
                None if state is OutboundDeliveryState.PENDING else str(uuid7())
            ),
            target=channel.canonical_target,
            target_kind=channel.target_kind,
            body=body,
            sender=sender or SenderIdentity(name="Alice"),
            provider_time_ms=None if state else at_ms,
            received_at_ms=None if state else at_ms + 100,
            created_at_ms=at_ms if state else None,
            provider_attempted_at_ms=at_ms if state else None,
            delivery_state=state,
            completed_at_ms=(
                at_ms
                if state in {OutboundDeliveryState.SENT, OutboundDeliveryState.FAILED}
                else None
            ),
            error_kind="provider" if state is OutboundDeliveryState.FAILED else None,
            error_message="发送失败" if state is OutboundDeliveryState.FAILED else None,
            metadata={"sender_kind": SenderKind.HUMAN.value} if not state else {},
        )
    )


@dataclass
class SearchData:
    database: SqliteDatabase
    scope: IStorageScope
    other: IStorageScope
    conversations: tuple[tuple[ChannelSession, Thread], ...]
    messages: dict[str, Message]


@pytest_asyncio.fixture
async def search_data() -> AsyncIterator[SearchData]:
    database = SqliteDatabase(max_idle_readers=1, max_readers=1)
    await database.start(timeout=3)
    try:
        scope = database.scope(str(uuid7()), "发布助理")
        other = database.scope(str(uuid7()), "另一位助理")
        conversations = (
            await _conversation(scope),
            await _conversation(scope),
            await _conversation(scope, review=Review.PENDING),
            await _conversation(scope, review=Review.DENIED),
        )
        messages = {
            "plan": await _message(
                scope,
                conversations[0],
                '部署计划发布到 STAGING；100%_done，使用 "AND"。',
                300,
            ),
            "short": await _message(
                scope,
                conversations[1],
                "部署 staging",
                200,
                sender=SenderIdentity(name="ALICE"),
            ),
            "id": await _message(
                scope,
                conversations[1],
                "发布方案 staging",
                150,
                sender=SenderIdentity(id="UserID", display_name="成员"),
            ),
            "id_case": await _message(
                scope,
                conversations[0],
                "发布方案 staging",
                100,
                sender=SenderIdentity(id="userid", display_name="成员"),
            ),
            "queued": await _message(
                scope,
                conversations[0],
                "部署计划 staging",
                250,
                state=OutboundDeliveryState.QUEUED,
            ),
            "sent": await _message(
                scope,
                conversations[1],
                "部署计划 staging staging staging",
                250,
                state=OutboundDeliveryState.SENT,
            ),
            "failed": await _message(
                scope,
                conversations[0],
                "部署计划 staging",
                1_000,
                state=OutboundDeliveryState.FAILED,
            ),
            "pending": await _message(
                scope,
                conversations[0],
                "部署计划 staging",
                1_001,
                state=OutboundDeliveryState.PENDING,
            ),
            "review_pending": await _message(
                scope, conversations[2], "部署计划 staging", 900
            ),
            "review_denied": await _message(
                scope, conversations[3], "部署计划 staging", 800
            ),
            "other": await _message(
                other, await _conversation(other), "部署计划 staging", 2_000
            ),
        }
        await scope.save_consumer_cursor(
            ConsumerCursor(
                thread_id=conversations[0][1].id,
                delivered_through_seq=messages["plan"].seq,
                updated_at_ms=400,
            )
        )
        yield SearchData(database, scope, other, conversations, messages)
    finally:
        await database.stop(timeout=3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "keys"),
    [
        ("部署计划", ("plan", "queued", "sent")),
        ("部署", ("plan", "short", "queued", "sent")),
        ("部署 staging", ("plan", "short", "queued", "sent")),
        ("100%_done", ("plan",)),
        ('"AND"', ("plan",)),
        ("部署 AND", ("plan",)),
    ],
)
async def test_literal_search_across_owned_readable_conversations(
    search_data: SearchData, query: str, keys: tuple[str, ...]
) -> None:
    data = search_data
    before = await data.scope.get_consumer_cursor(data.conversations[0][1].id)
    result = await data.scope.search_messages(MessageSearchRequest(query=query))
    assert {hit.message_id for hit in result.messages} == {
        data.messages[key].message_id for key in keys
    }
    for hit in result.messages:
        target = await data.scope.resolve_inbox_target(hit.canonical_target)
        assert hit.target == target.display_target
        assert len(hit.snippet) <= 240
    assert await data.scope.get_consumer_cursor(data.conversations[0][1].id) == before
    if query in ("部署计划", "部署", "部署 staging"):
        other = await data.other.search_messages(MessageSearchRequest(query=query))
        assert {hit.message_id for hit in other.messages} == {
            data.messages["other"].message_id
        }


@pytest.mark.asyncio
async def test_filters_time_order_and_pages_share_the_visible_scope(
    search_data: SearchData,
) -> None:
    data = search_data
    keys = ("plan", "sent", "queued", "short", "id", "id_case")
    found: list[str] = []
    request = MessageSearchRequest(query="staging", limit=2)
    while True:
        page = await data.scope.search_messages(request)
        found.extend(hit.message_id for hit in page.messages)
        if not page.has_more:
            break
        assert page.next_offset is not None
        request = replace(request, offset=page.next_offset)
    assert found == [data.messages[key].message_id for key in keys]
    bounded = await data.scope.search_messages(
        MessageSearchRequest(query="staging", after_ms=200, before_ms=250)
    )
    assert {hit.message_id for hit in bounded.messages} == {
        data.messages[key].message_id for key in ("short", "queued", "sent")
    }
    target = await data.scope.search_messages(
        MessageSearchRequest(
            raw_target=data.conversations[0][0].canonical_target,
            sender="@alice",
            sort="relevance",
        )
    )
    assert {hit.message_id for hit in target.messages} == {
        data.messages["plan"].message_id
    }
    assert target.sort == "time"
    bound_thread = await data.scope.search_messages(
        MessageSearchRequest(
            query="部署", raw_target=data.conversations[0][0].canonical_target
        ),
        thread_id=data.conversations[0][1].id,
    )
    assert bound_thread.messages and all(
        hit.thread_id == data.conversations[0][1].id for hit in bound_thread.messages
    )
    operator = await data.scope.search_messages(
        MessageSearchRequest(query="部署计划"), review=None
    )
    assert {
        data.messages[key].message_id for key in ("review_pending", "review_denied")
    } <= {hit.message_id for hit in operator.messages}
    relevant = await data.scope.search_messages(
        MessageSearchRequest(query="staging", sort="relevance")
    )
    assert {hit.message_id for hit in relevant.messages} == set(found)
    assert relevant.sort == "relevance"


@pytest.mark.asyncio
async def test_sender_selectors_preserve_ids_that_also_spell_a_handle(
    search_data: SearchData,
) -> None:
    data = search_data
    identity = data.messages["plan"].sender
    assert identity is not None
    message = await _message(
        data.scope,
        data.conversations[0],
        "确认发布记录",
        350,
        sender=SenderIdentity(id=identity.handle),
    )
    result = await data.scope.search_messages(
        MessageSearchRequest(sender="@" + identity.handle)
    )
    assert {message.message_id, data.messages["plan"].message_id} <= {
        hit.message_id for hit in result.messages
    }


@pytest.mark.asyncio
async def test_snippet_keeps_the_earliest_match_in_a_long_unicode_message(
    search_data: SearchData,
) -> None:
    data = search_data
    message = await _message(
        data.scope,
        data.conversations[0],
        "背景介绍。" * 80 + "部署计划" + "项目说明。" * 60 + "staging",
        500,
    )
    result = await data.scope.search_messages(
        MessageSearchRequest(query="部署计划 staging")
    )
    hit = next(hit for hit in result.messages if hit.message_id == message.message_id)
    assert "部署计划" in hit.snippet
    assert hit.snippet.startswith("…") and hit.snippet.endswith("…")
    assert len(hit.snippet) <= 240


@pytest.mark.asyncio
async def test_search_and_target_projection_use_one_reader_snapshot(
    search_data: SearchData,
) -> None:
    data = search_data
    channel, thread = data.conversations[0]
    async with data.database.reader() as session, session.transaction():
        repository = SqliteRepository(
            session, agent_id=data.scope.agent_id, agent_name=data.scope.agent_name
        )
        before = await repository.resolve_inbox_target(channel.canonical_target)
        await data.scope.save_channel_session(
            replace(
                channel,
                target_display_name="更新后的讨论",
                review=Review.PENDING,
                updated_at_ms=500,
            )
        )
        result = await repository.search_messages(
            MessageSearchRequest(query="部署计划", raw_target=channel.canonical_target)
        )
        assert result.messages
        assert all(
            hit.target == before.display_target and hit.thread_id == thread.id
            for hit in result.messages
        )
    result = await data.scope.search_messages(
        MessageSearchRequest(query="部署计划"), review=Review.PENDING
    )
    after = await data.scope.resolve_inbox_target(channel.canonical_target)
    assert all(
        hit.target == after.display_target
        for hit in result.messages
        if hit.thread_id == thread.id
    )


@pytest.mark.asyncio
async def test_index_changes_commit_and_rollback_with_message_history(
    search_data: SearchData,
) -> None:
    data = search_data
    message = data.messages["plan"]

    async def update(session: SqliteSession) -> None:
        await session.execute(
            "UPDATE messages SET body = ? WHERE message_id = ?",
            ("索引修改成功", message.message_id),
        )

    await data.database.transaction_write(update)
    result = await data.scope.search_messages(MessageSearchRequest(query="修改成功"))
    assert {hit.message_id for hit in result.messages} == {message.message_id}
    history = await data.scope.get_message(message.message_id)
    assert history is not None and result.messages[0].snippet == history.body
    await data.database.execute("BEGIN IMMEDIATE")
    try:
        await data.database.execute(
            "UPDATE messages SET body = ? WHERE message_id = ?",
            ("事务内临时修改", message.message_id),
        )
        await data.database.execute("ROLLBACK")
    except BaseException:
        await data.database.execute("ROLLBACK")
        raise
    restored = await data.scope.search_messages(MessageSearchRequest(query="修改成功"))
    assert {hit.message_id for hit in restored.messages} == {message.message_id}
    await data.database.execute(
        "UPDATE messages SET seq = (SELECT MAX(seq) + 1 FROM messages) WHERE message_id = ?",
        (message.message_id,),
    )
    moved = await data.scope.search_messages(MessageSearchRequest(query="修改成功"))
    assert {hit.message_id for hit in moved.messages} == {message.message_id}
    queued = data.messages["queued"]
    await data.scope.save_message(
        replace(
            queued,
            delivery_state=OutboundDeliveryState.FAILED,
            completed_at_ms=(queued.provider_attempted_at_ms or 0) + 1,
            error_kind="provider",
            error_message="发送失败",
        )
    )
    await data.database.execute(
        "DELETE FROM messages WHERE message_id = ?", (message.message_id,)
    )
    remaining = await data.scope.search_messages(MessageSearchRequest(query="部署计划"))
    assert {hit.message_id for hit in remaining.messages} == {
        data.messages["sent"].message_id
    }
    await data.database.execute(
        "INSERT INTO message_search(message_search, rank) VALUES ('integrity-check', 1)"
    )


@pytest.mark.asyncio
async def test_upgrade_indexes_existing_history_and_preserves_migration_ledger(
    tmp_path: Path,
) -> None:
    async with aiosqlite.connect(
        tmp_path / "existing.sqlite3", isolation_level=None
    ) as connection:
        connection.row_factory = aiosqlite.Row
        session = SqliteSession(connection)
        await session.execute("BEGIN IMMEDIATE")
        for migration in MIGRATIONS[:-1]:
            for statement in migration.statements:
                await session.execute(statement)
            await session.execute(
                "INSERT INTO schema_migrations(version, migration_name, checksum, applied_at_ms, duration_ms) VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )
        await session.execute("COMMIT")
        repository = SqliteRepository(
            session, agent_id=str(uuid7()), agent_name="历史助理"
        )
        channel = ChannelSession(
            id=str(uuid7()),
            channel="telegram",
            provider_thread_id=str(uuid7()),
            created_at_ms=1,
            updated_at_ms=1,
            review=Review.APPROVED,
        )
        thread = Thread(
            id=str(uuid7()),
            channel_session_id=channel.id,
            workspace_id=repository.agent_id or "",
            created_at_ms=1,
            updated_at_ms=1,
        )
        await repository.save_channel_session(channel)
        await repository.save_thread(thread)
        message = await repository.save_message(
            Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id=str(uuid7()),
                thread_id=thread.id,
                channel_session_id=channel.id,
                channel="telegram",
                provider_thread_id=channel.provider_thread_id,
                provider_message_id=str(uuid7()),
                target=channel.canonical_target,
                body="已有部署计划",
                sender=SenderIdentity(name="历史成员"),
                received_at_ms=2,
            )
        )
        before = await session.fetchall(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        )
        await session.execute("BEGIN IMMEDIATE")
        await apply_migrations(session)
        await session.execute("COMMIT")
        result = await repository.search_messages(
            MessageSearchRequest(query="部署计划")
        )
        assert {hit.message_id for hit in result.messages} == {message.message_id}
        after = await session.fetchall(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        )
        assert [tuple(row) for row in after[:-1]] == [tuple(row) for row in before]
        await session.execute("BEGIN IMMEDIATE")
        version = await apply_migrations(session)
        await session.execute("COMMIT")
        assert version == MIGRATIONS[-1].version
        await session.execute(
            "INSERT INTO message_search(message_search, rank) VALUES ('integrity-check', 1)"
        )


@pytest.mark.asyncio
async def test_query_budget_and_cancellation_return_a_clean_reader(
    search_data: SearchData,
) -> None:
    data = search_data
    sql = "WITH RECURSIVE counter(value) AS (VALUES(0) UNION ALL SELECT value + 1 FROM counter WHERE value < 100000000) SELECT SUM(value) FROM counter"
    async with data.database.reader() as session:
        with pytest.raises(SqliteQueryTimeoutError):
            async with session.transaction(), session.query_budget(0.01):
                await session.fetchone(sql)
    entered = asyncio.Event()

    async def cancelled() -> None:
        async with (
            data.database.reader() as session,
            session.transaction(),
            session.query_budget(5.0),
        ):
            entered.set()
            await session.fetchone(sql)

    task = asyncio.create_task(cancelled())
    await entered.wait()
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with data.database.reader() as session, session.transaction():
        row = await session.fetchone(
            "WITH RECURSIVE counter(value) AS (VALUES(0) UNION ALL SELECT value + 1 FROM counter WHERE value < 2000) SELECT SUM(value) FROM counter"
        )
        assert row is not None and row[0] > 0
    result = await data.scope.search_messages(MessageSearchRequest(query="部署计划"))
    assert result.messages
