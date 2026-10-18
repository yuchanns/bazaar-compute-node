from __future__ import annotations

lazy import asyncio
lazy from contextlib import asynccontextmanager
lazy from pathlib import Path

from bazaar_contrib_sqlite import SqliteConfig, open_database
from bazaar_contrib_storage import (
    AddColumn,
    Column,
    ColumnDefinition,
    CreateIndex,
    CreateTable,
    ForeignKey,
    Index,
    Insert,
    Select,
    Table,
)
from bazaar_core import Contrib, ContribHub, ContribSpec, Failed, load

from bazaar_contrib_schema import Dependency, Migration, ModuleSchema, Schema

NOTES = ModuleSchema(
    tables=("notes",),
    migrations=(
        Migration(
            version=1,
            name="notes",
            operations=(
                CreateTable(
                    table=Table(
                        name="notes",
                        columns=(
                            ColumnDefinition(name="id", type="integer"),
                            ColumnDefinition(name="body", type="text"),
                        ),
                        primary_key=("id",),
                    )
                ),
            ),
        ),
    ),
)
LABELS = ModuleSchema(
    tables=("labels",),
    dependencies=(Dependency(package="bazaar-contrib-schema", version=1),),
    migrations=(
        Migration(
            version=1,
            name="labels",
            operations=(
                CreateTable(
                    table=Table(
                        name="labels",
                        columns=(
                            ColumnDefinition(name="note_id", type="integer"),
                            ColumnDefinition(name="label", type="text"),
                        ),
                        foreign_keys=(
                            ForeignKey(
                                columns=("note_id",), table="notes", references=("id",)
                            ),
                        ),
                    )
                ),
            ),
        ),
    ),
)


@asynccontextmanager
async def registered(schema: Schema, module: ModuleSchema, entry: str = "schema"):
    @asynccontextmanager
    async def lifespan(ctx):
        async with schema.register(module):
            yield

    composition = await asyncio.to_thread(load, [ContribSpec(contrib=entry)])
    composition = composition.model_copy(
        update={
            "entries": tuple(
                item.model_copy(
                    update={
                        "name": f"test-{item.name}",
                        "definition": Contrib(lifespan=lifespan),
                    }
                )
                for item in composition.entries
            )
        }
    )
    async with ContribHub() as hub, hub.enable(composition):
        yield hub


async def test_module_dependencies_and_restart_upgrade_preserve_data_and_ledger(
    tmp_path: Path,
):
    config = SqliteConfig(path=tmp_path / "modules.sqlite3")
    async with open_database(config) as database:
        schema = Schema(database)
        await schema.start()
        async with registered(schema, NOTES), registered(schema, LABELS, "storage"):
            await database.insert(
                Insert(table="notes", rows=({"id": 1, "body": "keep this note"},))
            )
            await database.insert(
                Insert(table="labels", rows=({"note_id": 1, "label": "inbox"},))
            )
            assert set(schema.modules) == {
                "bazaar-contrib-schema",
                "bazaar-contrib-storage",
            }
        ledger = await database.select(Select(table="contrib_schema_migrations"))
    upgraded = ModuleSchema(
        tables=NOTES.tables,
        migrations=(
            *NOTES.migrations,
            Migration(
                version=2,
                name="note_priority",
                operations=(
                    AddColumn(
                        table="notes",
                        column=ColumnDefinition(
                            name="priority", type="integer", default=1
                        ),
                    ),
                    CreateIndex(
                        table="notes",
                        index=Index(name="note_priority", columns=("priority",)),
                    ),
                ),
            ),
        ),
    )
    for _ in range(2):
        async with open_database(config) as database:
            schema = Schema(database)
            await schema.start()
            async with (
                registered(schema, upgraded),
                registered(schema, LABELS, "storage"),
            ):
                rows = await database.select(Select(table="notes"))
                assert rows[0]["body"] == "keep this note"
                assert rows[0]["priority"] == 1
                labels = await database.select(Select(table="labels"))
                assert labels[0]["label"] == "inbox"
                history = await database.select(
                    Select(table="contrib_schema_migrations")
                )
                for entry in ledger:
                    assert entry in history
                assert len(history) == 3


async def test_failed_unique_index_migration_can_resume_after_resolving_data(
    tmp_path: Path,
):
    async with open_database(
        SqliteConfig(path=tmp_path / "unique.sqlite3")
    ) as database:
        schema = Schema(database)
        await schema.start()
        async with registered(schema, NOTES):
            await database.insert(
                Insert(
                    table="notes",
                    rows=(
                        {"id": 1, "body": "duplicate"},
                        {"id": 2, "body": "duplicate"},
                    ),
                )
            )
        unique = ModuleSchema(
            tables=NOTES.tables,
            migrations=(
                *NOTES.migrations,
                Migration(
                    version=2,
                    name="unique_note",
                    operations=(
                        CreateIndex(
                            table="notes",
                            index=Index(
                                name="unique_note", columns=("body",), unique=True
                            ),
                        ),
                    ),
                ),
            ),
        )
        async with registered(schema, unique) as hub:
            assert any(isinstance(item.state, Failed) for item in hub.snapshots)
        from bazaar_contrib_storage import Compare, Delete, Value

        await database.delete(
            Delete(
                table="notes",
                where=Compare(
                    left=Column(name="id"), operator="=", right=Value(value=2)
                ),
            )
        )
        async with registered(schema, unique):
            rows = await database.select(Select(table="notes"))
            assert rows[0]["body"] == "duplicate"
            assert (
                len(await database.select(Select(table="contrib_schema_migrations")))
                == 2
            )


async def test_table_ownership_survives_registration_exit(tmp_path: Path):
    async with open_database(
        SqliteConfig(path=tmp_path / "ownership.sqlite3")
    ) as database:
        schema = Schema(database)
        await schema.start()
        async with registered(schema, NOTES):
            await database.insert(
                Insert(table="notes", rows=({"id": 1, "body": "owned"},))
            )
        schema = Schema(database)
        await schema.start()
        async with registered(schema, NOTES, "sqlite") as hub:
            assert any(isinstance(item.state, Failed) for item in hub.snapshots)
        async with registered(schema, NOTES):
            assert (await database.select(Select(table="notes")))[0]["body"] == "owned"
