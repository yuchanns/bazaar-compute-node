from __future__ import annotations

lazy from pathlib import Path

import pytest
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
    MigrationError,
    Select,
    StorageIntegrityError,
    Table,
)

from bazaar_contrib_schema import Dependency, Migration, ModuleSchema, Schema

NOTES = ModuleSchema(
    package="example-notes",
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
    package="example-labels",
    tables=("labels",),
    dependencies=(Dependency(package="example-notes", version=1),),
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


async def test_module_dependencies_and_restart_upgrade_preserve_data_and_ledger(
    tmp_path: Path,
):
    config = SqliteConfig(path=tmp_path / "modules.sqlite3")
    async with open_database(config) as database:
        schema = Schema(database)
        await schema.start()
        async with schema.register(NOTES), schema.register(LABELS):
            await database.insert(
                Insert(table="notes", rows=({"id": 1, "body": "keep this note"},))
            )
            await database.insert(
                Insert(table="labels", rows=({"note_id": 1, "label": "inbox"},))
            )
            registered = {module.package for module in schema.modules}
            assert registered == {NOTES.package, LABELS.package}
        ledger = await database.select(Select(table="contrib_schema_migrations"))
    upgraded = ModuleSchema(
        package=NOTES.package,
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
            async with schema.register(upgraded), schema.register(LABELS):
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
        async with schema.register(NOTES):
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
            package=NOTES.package,
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
        with pytest.raises(StorageIntegrityError):
            async with schema.register(unique):
                pass
        from bazaar_contrib_storage import Compare, Delete, Value

        await database.delete(
            Delete(
                table="notes",
                where=Compare(
                    left=Column(name="id"), operator="=", right=Value(value=2)
                ),
            )
        )
        async with schema.register(unique):
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
        async with schema.register(NOTES):
            await database.insert(
                Insert(table="notes", rows=({"id": 1, "body": "owned"},))
            )
        schema = Schema(database)
        await schema.start()
        other = ModuleSchema(
            package="other-notes", tables=NOTES.tables, migrations=NOTES.migrations
        )
        with pytest.raises(MigrationError):
            async with schema.register(other):
                pass
        async with schema.register(NOTES):
            assert (await database.select(Select(table="notes")))[0]["body"] == "owned"
