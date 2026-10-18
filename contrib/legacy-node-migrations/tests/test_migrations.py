from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import sys
lazy import zlib
lazy from pathlib import Path

import pytest
from bazaar_contrib_schema import SCHEMA, Schema
from bazaar_contrib_sqlite import SqliteConfig, open_database
from bazaar_contrib_storage import Select

from bazaar_contrib_legacy_node_migrations import LEGACY_NODE_MIGRATIONS


@pytest.fixture
def modules():
    from bazaar_contrib_agent.schema import MODULE as agent
    from bazaar_contrib_collaboration.schema import MODULE as collaboration
    from bazaar_contrib_messages_node.schema import MODULE as messages
    from bazaar_contrib_reminder.schema import MODULE as reminder

    return {
        "agent": agent,
        "collaboration": collaboration,
        "messages-node": messages,
        "reminder": reminder,
    }


@asynccontextmanager
async def registered(schema, modules):
    from bazaar_core import (
        Context,
        Contrib,
        ContribEntry,
        ContribHub,
        ContribSpec,
        load,
    )

    @asynccontextmanager
    async def provide(ctx: Context):
        ctx.offer(SCHEMA, schema)
        yield

    @asynccontextmanager
    async def register(ctx: Context):
        async with ctx.accept(SCHEMA).register(modules[ctx.name]):
            yield

    composition = await asyncio.to_thread(
        load,
        [ContribSpec(contrib=name) for name in ("legacy-node-migrations", *modules)],
    )
    composition = composition.model_copy(
        update={
            "entries": (
                ContribEntry(
                    name="schema", definition=Contrib(offers=[SCHEMA], lifespan=provide)
                ),
                *(
                    entry.model_copy(
                        update={
                            "definition": Contrib(
                                accepts=[SCHEMA, LEGACY_NODE_MIGRATIONS],
                                lifespan=register,
                            )
                        }
                    )
                    if entry.name in modules
                    else entry
                    for entry in composition.entries
                ),
            ),
        }
    )
    async with ContribHub() as hub, hub.enable(composition):
        yield


@pytest.mark.parametrize("version", range(1, 33))
async def test_published_message_upgrade_preserves_business_data_and_original_ledger(
    tmp_path: Path,
    version: int,
    modules,
):
    root = (await asyncio.to_thread(Path(__file__).resolve)).parents[3]
    path = tmp_path / "node.sqlite3"
    expected = tmp_path / "expected.sqlite3"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "contrib/legacy-node-migrations/tests/fixtures/history.py"),
        str(root / "src"),
        str(version),
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
        schema = Schema(database)
        await schema.start()
        async with registered(schema, modules):
            for table in (
                table for module in modules.values() for table in module.tables
            ):
                if await reference.table_exists(table):
                    rows = await database.select(Select(table=table))
                    expected_rows = await reference.select(Select(table=table))
                    assert sorted(map(repr, rows)) == sorted(
                        map(repr, expected_rows)
                    ), table
        assert await database.select(Select(table="schema_migrations")) == original


async def test_new_database_uses_current_baseline_and_retains_new_data(
    tmp_path: Path, modules
):
    from bazaar_contrib_storage import Insert

    path = tmp_path / "fresh.sqlite3"
    async with open_database(SqliteConfig(path=path)) as database:
        schema = Schema(database)
        await schema.start()
        async with registered(schema, modules):
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
        async with registered(schema, modules):
            rows = await database.select(Select(table="settings"))
            assert rows
            history = await database.select(Select(table="contrib_schema_migrations"))
            assert {row["package"] for row in history} == {
                f"bazaar-contrib-{name}" for name in modules
            }
            assert all(row["version"] == 1 for row in history)


async def test_current_messages_keep_search_in_sync_with_the_sequence_key(
    tmp_path: Path, modules
):
    import bazaar_contrib_storage as s

    async with open_database(
        SqliteConfig(path=tmp_path / "search.sqlite3")
    ) as database:
        schema = Schema(database)
        await schema.start()
        async with registered(schema, modules):
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


async def test_registered_03_database_accepts_owned_reminder_versions_and_restart(
    tmp_path: Path, modules
):
    import bazaar_contrib_storage as s
    from bazaar_contrib_schema import Migration, ModuleSchema

    fixture = Path(__file__).parent / "fixtures/node-v03.sqlite3.zlib"
    data = await asyncio.to_thread(fixture.read_bytes)
    path = tmp_path / "registered-03.sqlite3"
    await asyncio.to_thread(path.write_bytes, zlib.decompress(data))
    upgraded = {
        name: ModuleSchema(
            tables=module.tables,
            migrations=(
                *module.migrations,
                Migration(
                    version=2,
                    name="reminder_priority",
                    operations=(
                        s.AddColumn(
                            table="reminders",
                            column=s.ColumnDefinition(
                                name="priority", type="integer", default=0
                            ),
                        ),
                    ),
                ),
            ),
            baseline=module.baseline,
            predecessor=module.predecessor,
        )
        if name == "reminder"
        else module
        for name, module in modules.items()
    }
    async with open_database(SqliteConfig(path=path)) as database:
        previous = await database.select(Select(table="contrib_schema_migrations"))
        original = await database.select(Select(table="schema_migrations"))
        messages = await database.select(Select(table="messages"))
        settings = await database.select(Select(table="settings"))
        schema = Schema(database)
        await schema.start()
        async with registered(schema, dict(reversed(upgraded.items()))):
            reminders = await database.select(Select(table="reminders"))
            assert reminders and reminders[0]["priority"] == 0
            await database.update(
                s.Update(table="reminders", values={"priority": s.Value(value=5)})
            )
        history = await database.select(Select(table="contrib_schema_migrations"))
        for row in previous:
            assert row in history
    for _ in range(2):
        async with open_database(SqliteConfig(path=path)) as database:
            schema = Schema(database)
            await schema.start()
            async with registered(schema, dict(reversed(upgraded.items()))):
                reminders = await database.select(Select(table="reminders"))
                assert reminders[0]["priority"] == 5
                assert await database.select(Select(table="messages")) == messages
                assert await database.select(Select(table="settings")) == settings
                assert (
                    await database.select(Select(table="schema_migrations")) == original
                )
                assert (
                    await database.select(Select(table="contrib_schema_migrations"))
                    == history
                )
                owners = await database.select(Select(table="contrib_schema_tables"))
                assert {row["name"]: row["package"] for row in owners} == {
                    table: f"bazaar-contrib-{name}"
                    for name, module in modules.items()
                    for table in module.tables
                }


async def test_partial_03_history_resumes_under_installed_identity_and_keeps_old_records(
    tmp_path: Path, modules
):
    from bazaar_contrib_storage import Insert

    fixture = Path(__file__).parent / "fixtures/node-v03-v17.sqlite3.zlib"
    data = await asyncio.to_thread(fixture.read_bytes)
    path = tmp_path / "partial-03.sqlite3"
    await asyncio.to_thread(path.write_bytes, zlib.decompress(data))
    expected = tmp_path / "expected.sqlite3"
    data = await asyncio.to_thread(
        fixture.with_name("node-v03.sqlite3.zlib").read_bytes
    )
    await asyncio.to_thread(expected.write_bytes, zlib.decompress(data))
    async with (
        open_database(SqliteConfig(path=path)) as database,
        open_database(SqliteConfig(path=expected)) as reference,
    ):
        previous = await database.select(Select(table="contrib_schema_migrations"))
        original = await database.select(Select(table="schema_migrations"))
        for run in range(2):
            schema = Schema(database)
            await schema.start()
            async with registered(schema, modules):
                history = await database.select(
                    Select(table="contrib_schema_migrations")
                )
                assert all(row in history for row in previous)
                assert any(
                    row["package"] == "bazaar-contrib-legacy-node-migrations"
                    and row["version"] == 32
                    for row in history
                )
                assert (
                    await database.select(Select(table="schema_migrations")) == original
                )
                if run == 0:
                    for module in modules.values():
                        for table in module.tables:
                            if await reference.table_exists(table):
                                rows = await database.select(Select(table=table))
                                expected_rows = await reference.select(
                                    Select(table=table)
                                )
                                assert sorted(map(repr, rows)) == sorted(
                                    map(repr, expected_rows)
                                ), table
                    await database.insert(
                        Insert(
                            table="settings",
                            rows=(
                                {
                                    "agent_id": "upgrade",
                                    "key": "welcome",
                                    "value": "kept",
                                    "updated_at_ms": 1,
                                },
                            ),
                        )
                    )
                assert any(
                    row["value"] == "kept"
                    for row in await database.select(Select(table="settings"))
                )


async def test_business_lifespans_resume_registered_nodes_and_preserve_rows(
    tmp_path: Path, modules
):
    from bazaar_contrib_collaboration import COLLABORATION
    from bazaar_contrib_messages import MESSAGES
    from bazaar_contrib_reminder import REMINDER
    from bazaar_contrib_schema import SCHEMA
    from bazaar_contrib_storage import DB
    from bazaar_core import (
        Context,
        Contrib,
        ContribEntry,
        ContribHub,
        ContribSpec,
        load,
    )

    fixture = Path(__file__).parent / "fixtures/node-v03.sqlite3.zlib"
    data = await asyncio.to_thread(fixture.read_bytes)
    path = tmp_path / "lifespans.sqlite3"
    await asyncio.to_thread(path.write_bytes, zlib.decompress(data))
    active = []

    @asynccontextmanager
    async def consume(ctx: Context):
        active.append((ctx.accept(SCHEMA), ctx.accept(DB)))
        yield

    for _ in range(2):
        active.clear()
        composition = await asyncio.to_thread(
            load,
            [
                ContribSpec(contrib=entry, config=config)
                for entry, config in (
                    ("storage", {}),
                    ("sqlite", {"path": str(path)}),
                    ("database", {"backend": "sqlite"}),
                    ("schema", {}),
                    ("legacy-node-migrations", {}),
                    ("audit", {}),
                    ("timer-wheel", {}),
                    ("agent", {}),
                    ("messages-node", {}),
                    ("collaboration", {}),
                    ("reminder", {}),
                )
            ],
        )
        composition = composition.model_copy(
            update={
                "entries": (
                    *composition.entries,
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(
                            accepts=[SCHEMA, DB, MESSAGES, COLLABORATION, REMINDER],
                            lifespan=consume,
                        ),
                    ),
                )
            }
        )
        async with ContribHub() as hub, hub.enable(composition):
            schema, database = active[0]
            assert {f"bazaar-contrib-{name}" for name in modules}.issubset(
                schema.modules
            )
            assert await database.select(Select(table="messages"))
            assert await database.select(Select(table="reminders"))
            owners = await database.select(Select(table="contrib_schema_tables"))
            assert {row["name"]: row["package"] for row in owners} == {
                table: f"bazaar-contrib-{name}"
                for name, module in modules.items()
                for table in module.tables
            }
