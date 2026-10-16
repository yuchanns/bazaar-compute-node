from __future__ import annotations

lazy import asyncio
lazy import sys
lazy from pathlib import Path

from bazaar_contrib_schema import ModuleSchema, Schema
from bazaar_contrib_sqlite import SqliteConfig, open_database
from bazaar_contrib_storage import Select

from bazaar_contrib_node_migrations import MODULE


async def test_published_message_upgrade_preserves_business_data_and_original_ledger(
    tmp_path: Path,
):
    root = (await asyncio.to_thread(Path(__file__).resolve)).parents[3]
    path = tmp_path / "node.sqlite3"
    expected = tmp_path / "expected.sqlite3"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "contrib/node-migrations/tests/fixtures/history.py"),
        str(root / "src"),
        "17",
        str(path),
        str(expected),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    async with (
        open_database(SqliteConfig(path=path)) as database,
        open_database(SqliteConfig(path=expected)) as reference,
    ):
        original = await database.select(Select(table="schema_migrations"))
        # Keep the released bridge fixed when new business migrations are added.
        published = ModuleSchema(
            package=MODULE.package,
            tables=MODULE.tables,
            migrations=MODULE.migrations[:32],
            progress=MODULE.progress,
        )
        schema = Schema(database)
        await schema.start()
        async with schema.register(published):
            for table in published.tables:
                if await reference.table_exists(table):
                    rows = await database.select(Select(table=table))
                    expected_rows = await reference.select(Select(table=table))
                    assert sorted(map(repr, rows)) == sorted(
                        map(repr, expected_rows)
                    ), table
        assert await database.select(Select(table="schema_migrations")) == original


async def test_new_database_uses_current_baseline_and_retains_new_data(tmp_path: Path):
    from bazaar_contrib_storage import Insert

    path = tmp_path / "fresh.sqlite3"
    async with open_database(SqliteConfig(path=path)) as database:
        schema = Schema(database)
        await schema.start()
        async with schema.register(MODULE):
            await database.insert(
                Insert(
                    table="settings",
                    rows=(
                        {
                            "agent_id": "agent",
                            "key": "welcome",
                            "value": "Hello",
                            "updated_at_ms": 1,
                        },
                    ),
                )
            )
        async with schema.register(MODULE):
            rows = await database.select(Select(table="settings"))
            assert rows
            history = await database.select(Select(table="contrib_schema_migrations"))
            assert len(history) == 1
            assert history[0]["version"] == MODULE.version


async def test_current_messages_keep_search_in_sync_with_the_sequence_key(
    tmp_path: Path,
):
    import bazaar_contrib_storage as s

    async with open_database(
        SqliteConfig(path=tmp_path / "search.sqlite3")
    ) as database:
        schema = Schema(database)
        await schema.start()
        async with schema.register(MODULE):
            await database.insert(
                s.Insert(
                    table="messages",
                    rows=(
                        {
                            "message_id": "message",
                            "seq": 100,
                            "direction": "inbound",
                            "agent_id": "agent",
                            "thread_id": "thread",
                            "channel_session_id": "channel",
                            "channel": "test",
                            "provider_thread_id": "test:peer",
                            "received_at_ms": 1,
                            "message_type": "text",
                            "target": "dm:channel",
                            "target_kind": "dm",
                            "body": "An archived message",
                            "mentions_agent": 0,
                            "notifies_runtime": 1,
                        },
                    ),
                )
            )
            query = s.Select(
                table="messages",
                where=s.Search(
                    index="message_search",
                    table="messages",
                    columns=("body",),
                    row_key="seq",
                    terms=("archived",),
                ),
            )
            assert (await database.select(query))[0]["seq"] == 100
            await database.update(
                s.Update(
                    table="messages",
                    values={"body": s.Value(value="Updated archived message")},
                )
            )
            assert (await database.select(query))[0][
                "body"
            ] == "Updated archived message"
