"""Ordinary data workflow shared with the independent PostgreSQL provider."""

from bazaar_contrib_storage import (
    AddColumn,
    All,
    AnyOf,
    Binary,
    Call,
    Column,
    ColumnDefinition,
    Compare,
    Conflict,
    Contains,
    CreateIndex,
    CreateTable,
    Database,
    Delete,
    DropIndex,
    ForeignKey,
    In,
    Index,
    Insert,
    IsNull,
    Join,
    Not,
    Search,
    Select,
    Session,
    Sort,
    Table,
    Update,
    Value,
)


async def workflow(database: Database) -> dict[str, object]:
    await database.apply_schema(
        (
            CreateTable(
                table=Table(
                    name="workspaces",
                    columns=(
                        ColumnDefinition(name="id", type="integer"),
                        ColumnDefinition(name="name", type="text"),
                    ),
                    primary_key=("id",),
                )
            ),
            CreateTable(
                table=Table(
                    name="notes",
                    columns=(
                        ColumnDefinition(name="id", type="integer", generated=True),
                        ColumnDefinition(name="workspace_id", type="integer"),
                        ColumnDefinition(name="body", type="text"),
                        ColumnDefinition(name="score", type="integer", default=0),
                    ),
                    primary_key=("id",),
                    foreign_keys=(
                        ForeignKey(
                            columns=("workspace_id",),
                            table="workspaces",
                            references=("id",),
                        ),
                    ),
                )
            ),
            CreateIndex(
                table="workspaces",
                index=Index(name="workspace_names", columns=("name",), unique=True),
            ),
            CreateIndex(
                table="notes",
                index=Index(name="notes_search", columns=("body",), kind="search"),
            ),
            CreateIndex(
                table="notes",
                index=Index(
                    name="notes_score",
                    columns=("score",),
                    where=Compare(
                        left=Column(name="score"), operator=">", right=Value(value=0)
                    ),
                ),
            ),
        )
    )
    await database.apply_schema(
        (
            CreateIndex(
                table="workspaces",
                index=Index(name="workspace_names", columns=("name",), unique=True),
                if_not_exists=True,
            ),
            CreateIndex(
                table="notes",
                index=Index(name="notes_search", columns=("body",), kind="search"),
                if_not_exists=True,
            ),
        )
    )
    await database.insert(
        Insert(
            table="workspaces",
            rows=({"id": 1, "name": "Notes"}, {"id": 2, "name": "Archive"}),
        )
    )
    inserted = await database.insert(
        Insert(
            table="notes",
            rows=(
                {"workspace_id": 1, "body": "Shanghai database 上海", "score": 3},
                {
                    "workspace_id": 1,
                    "body": 'SQL quote "literal" 100%_safe',
                    "score": 5,
                },
                {"workspace_id": 2, "body": "Archived database", "score": 1},
            ),
            returning=("id", "body"),
        )
    )
    assert inserted.count == 3
    ids = tuple(row["id"] for row in inserted.rows)
    await database.insert(
        Insert(
            table="notes",
            rows=(
                {
                    "id": ids[0],
                    "workspace_id": 1,
                    "body": "Shanghai database 上海",
                    "score": 4,
                },
            ),
            conflict=Conflict(columns=("id",), update=("score",)),
            returning=("id",),
        )
    )
    notes = await database.select(
        Select(
            table="notes",
            columns={"id": Column(name="id"), "body": Column(name="body")},
            where=In(expression=Column(name="id"), values=ids),
            order_by=(Sort(expression=Column(name="id")),),
        )
    )
    assert len(notes) == 3
    associated = await database.select(
        Select(
            table="notes",
            alias="n",
            columns={
                "body": Column(table="n", name="body"),
                "workspace": Column(table="w", name="name"),
            },
            joins=(
                Join(
                    table="workspaces",
                    alias="w",
                    on=Compare(
                        left=Column(table="n", name="workspace_id"),
                        operator="=",
                        right=Column(table="w", name="id"),
                    ),
                ),
            ),
            where=In(expression=Column(table="n", name="workspace_id"), values=(1,)),
            order_by=(
                Sort(expression=Column(table="n", name="score"), descending=True),
                Sort(expression=Column(table="n", name="id")),
            ),
            limit=1,
            offset=1,
        )
    )
    assert associated[0]["body"] == "Shanghai database 上海"
    assert associated[0]["workspace"] == "Notes"
    grouped = await database.select(
        Select(
            table="notes",
            columns={
                "workspace": Column(name="workspace_id"),
                "total": Call(name="count"),
                "score": Call(name="sum", arguments=(Column(name="score"),)),
            },
            group_by=(Column(name="workspace_id"),),
            having=Compare(left=Call(name="count"), operator=">", right=Value(value=1)),
        )
    )
    assert grouped[0]["total"] == 2
    assert grouped[0]["score"] == 9
    calculated = await database.select(
        Select(
            table="notes",
            columns={
                "remainder": Binary(
                    left=Column(name="score"), operator="%", right=Value(value=2)
                ),
                "size": Call(name="length", arguments=(Column(name="body"),)),
            },
            where=Compare(
                left=Column(name="id"), operator="=", right=Value(value=ids[0])
            ),
        )
    )
    assert calculated[0]["remainder"] == 0
    assert calculated[0]["size"] == len("Shanghai database 上海")
    matches = await database.select(
        Select(
            table="notes",
            where=Search(
                index="notes_search",
                table="notes",
                columns=("body",),
                terms=("DATABASE", "上海"),
            ),
        )
    )
    assert matches[0]["id"] == ids[0]
    literal = await database.select(
        Select(
            table="notes",
            where=Search(
                index="notes_search",
                table="notes",
                columns=("body",),
                terms=('"literal"', "100%_safe"),
            ),
        )
    )
    assert literal[0]["id"] == ids[1]

    async def change(session: Session):
        await session.update(
            Update(
                table="notes",
                values={
                    "score": Binary(
                        left=Column(name="score"), operator="+", right=Value(value=1)
                    ),
                    "body": Value(value="Changed searchable"),
                },
                where=Compare(
                    left=Column(name="id"), operator="=", right=Value(value=ids[0])
                ),
            )
        )
        return await session.select(
            Select(
                table="notes",
                where=All(
                    conditions=(
                        Compare(
                            left=Column(name="id"),
                            operator="=",
                            right=Value(value=ids[0]),
                        ),
                        Search(
                            index="notes_search",
                            table="notes",
                            columns=("body",),
                            terms=("searchable",),
                        ),
                    )
                ),
            )
        )

    changed = await database.transaction(change)
    assert changed[0]["score"] == 5
    deleted = await database.delete(
        Delete(
            table="notes",
            where=Compare(
                left=Column(name="id"), operator="=", right=Value(value=ids[2])
            ),
            returning=("body",),
        )
    )
    assert deleted.rows[0]["body"] == "Archived database"
    empty = await database.select(
        Select(
            table="workspaces",
            alias="w",
            columns={
                "name": Column(table="w", name="name"),
                "body": Call(
                    name="coalesce",
                    arguments=(
                        Column(table="n", name="body"),
                        Value(value="empty workspace"),
                    ),
                ),
            },
            joins=(
                Join(
                    table="notes",
                    alias="n",
                    kind="left",
                    on=Compare(
                        left=Column(table="w", name="id"),
                        operator="=",
                        right=Column(table="n", name="workspace_id"),
                    ),
                ),
            ),
            where=IsNull(expression=Column(table="n", name="id")),
        )
    )
    assert empty[0]["name"] == "Archive"
    assert empty[0]["body"] == "empty workspace"
    remaining = await database.select(
        Select(table="notes", columns={"total": Call(name="count")})
    )
    assert remaining[0]["total"] == 2

    await database.apply_schema(
        (
            AddColumn(
                table="notes",
                column=ColumnDefinition(
                    name="tag", type="text", default="reader's {{ tag }}"
                ),
            ),
            AddColumn(
                table="notes",
                column=ColumnDefinition(
                    name="payload", type="blob", default=b"\x00'\xff"
                ),
            ),
            AddColumn(
                table="notes",
                column=ColumnDefinition(name="active", type="integer", default=True),
            ),
            DropIndex(table="notes", name="notes_score"),
            DropIndex(table="notes", name="notes_search"),
        )
    )
    await database.insert(
        Insert(
            table="notes",
            rows=({"workspace_id": 1, "body": "Rebuilt searchable index", "score": 9},),
        )
    )
    await database.apply_schema(
        (
            CreateIndex(
                table="notes",
                index=Index(name="notes_search", columns=("body",), kind="search"),
            ),
        )
    )
    await database.insert(
        Insert(
            table="notes",
            rows=({"id": ids[0], "workspace_id": 1, "body": "Duplicate", "score": 0},),
            conflict=Conflict(columns=("id",)),
        )
    )
    label = "reader's {{ label }} ? $1 %_"
    filtered = await database.select(
        Select(
            table="notes",
            alias="n",
            columns={
                "label": Value(value=label),
                "body": Column(table="n", name="body"),
                "tag": Column(table="n", name="tag"),
                "payload": Column(table="n", name="payload"),
                "active": Column(table="n", name="active"),
            },
            joins=(
                Join(
                    table="workspaces",
                    alias="w",
                    on=All(
                        conditions=(
                            Compare(
                                left=Column(table="w", name="id"),
                                operator="=",
                                right=Column(table="n", name="workspace_id"),
                            ),
                            Compare(
                                left=Column(table="w", name="name"),
                                operator="=",
                                right=Value(value="Notes"),
                            ),
                        )
                    ),
                ),
            ),
            where=All(
                conditions=(
                    AnyOf(
                        conditions=(
                            Contains(
                                expression=Column(table="n", name="body"),
                                text="searchable",
                            ),
                            In(expression=Column(table="n", name="id"), values=()),
                        )
                    ),
                    Not(
                        condition=Compare(
                            left=Column(table="n", name="score"),
                            operator="<",
                            right=Value(value=0),
                        )
                    ),
                    IsNull(expression=Column(table="n", name="tag"), exclude=True),
                )
            ),
            order_by=(
                Sort(
                    expression=Binary(
                        left=Column(table="n", name="score"),
                        operator="+",
                        right=Value(value=100),
                    ),
                    descending=True,
                ),
            ),
            limit=1,
            offset=1,
        )
    )
    assert filtered[0]["label"] == label
    assert filtered[0]["body"] == "Changed searchable"
    assert filtered[0]["tag"] == "reader's {{ tag }}"
    assert filtered[0]["payload"] == b"\x00'\xff"
    assert filtered[0]["active"] == 1
    rebuilt = await database.select(
        Select(
            table="notes",
            where=Search(
                index="notes_search",
                table="notes",
                columns=("body",),
                terms=("Rebuilt",),
            ),
        )
    )
    assert rebuilt[0]["body"] == "Rebuilt searchable index"
    page = await database.select(
        Select(
            table="notes",
            order_by=(Sort(expression=Column(name="score"), descending=True),),
            offset=1,
        )
    )
    assert len(page) == 2
    workspaces = await database.select(
        Select(
            table="notes",
            columns={"workspace_id": Column(name="workspace_id")},
            distinct=True,
        )
    )
    assert len(workspaces) == 1
    return {
        "batch": inserted.count,
        "association": associated[0]["workspace"],
        "total": remaining[0]["total"],
        "search": matches[0]["body"],
    }


async def migration_workflow(database: Database):
    """Upgrade an article archive through the same backend-neutral operations."""
    import bazaar_contrib_storage as s

    await database.apply_schema(
        (
            s.CreateTable(
                table=s.Table(
                    name="authors",
                    columns=(s.ColumnDefinition(name="id", type="text"),),
                    primary_key=("id",),
                )
            ),
            s.CreateTable(
                table=s.Table(
                    name="article_legacy",
                    columns=(
                        s.ColumnDefinition(name="id", type="text"),
                        s.ColumnDefinition(name="created", type="integer"),
                        s.ColumnDefinition(name="body", type="text"),
                    ),
                    primary_key=("id",),
                )
            ),
            s.CreateIndex(
                table="article_legacy",
                index=s.Index(
                    name="article_order",
                    order_by=(
                        s.Sort(
                            expression=s.Call(
                                name="lower", arguments=(s.Column(name="body"),)
                            )
                        ),
                        s.Sort(expression=s.Column(name="created"), descending=True),
                    ),
                ),
            ),
        )
    )
    await database.insert(s.Insert(table="authors", rows=({"id": "owner"},)))
    await database.insert(
        s.Insert(
            table="article_legacy",
            rows=(
                {"id": "later", "created": 2000, "body": "Later article"},
                {"id": "earlier", "created": 1000, "body": "Earlier article"},
            ),
        )
    )

    async def upgrade(session: s.Session):
        await session.apply_schema(
            (
                s.CreateTable(
                    table=s.Table(
                        name="article_ranked",
                        columns=(
                            s.ColumnDefinition(name="id", type="text"),
                            s.ColumnDefinition(
                                name="rank",
                                type="integer",
                                unique=True,
                                check=s.Compare(
                                    left=s.Column(name="rank"),
                                    operator=">",
                                    right=s.Value(value=0),
                                ),
                            ),
                            s.ColumnDefinition(name="body", type="text"),
                            s.ColumnDefinition(
                                name="author",
                                type="text",
                                references=s.Reference(table="authors", column="id"),
                            ),
                            s.ColumnDefinition(
                                name="category", type="text", nullable=True
                            ),
                            s.ColumnDefinition(
                                name="obsolete", type="integer", nullable=True
                            ),
                        ),
                        primary_key=("id",),
                        checks=(
                            s.Compare(
                                left=s.Call(
                                    name="length", arguments=(s.Column(name="body"),)
                                ),
                                operator=">",
                                right=s.Value(value=0),
                            ),
                        ),
                    )
                ),
            )
        )
        await session.copy(
            s.Copy(
                table="article_ranked",
                query=s.Select(
                    table="article_legacy",
                    columns={
                        "id": s.Column(name="id"),
                        "rank": s.RowNumber(
                            order_by=(s.Sort(expression=s.Column(name="created")),)
                        ),
                        "body": s.Column(name="body"),
                        "author": s.Value(value="owner"),
                    },
                ),
            )
        )
        await session.apply_schema(
            (
                s.RenameTable(table="article_ranked", name="articles"),
                s.DropIndex(table="article_legacy", name="article_order"),
                s.DropTable(table="article_legacy"),
                s.RenameColumn(table="articles", column="body", name="content"),
                s.DropColumn(table="articles", column="obsolete"),
                s.CreateTrigger(
                    name="article_category",
                    table="articles",
                    event="insert",
                    when=s.IsNull(expression=s.Column(name="category", table="new")),
                    action=s.Update(
                        table="articles",
                        values={"category": s.Value(value="general")},
                        where=s.Compare(
                            left=s.Column(name="id"),
                            operator="=",
                            right=s.Column(name="id", table="new"),
                        ),
                    ),
                ),
            )
        )
        await session.insert(
            s.Insert(
                table="articles",
                rows=(
                    {
                        "id": "new",
                        "rank": 3,
                        "content": "New article",
                        "author": "owner",
                    },
                ),
            )
        )
        rows = await session.select(
            s.Select(
                table="articles", order_by=(s.Sort(expression=s.Column(name="rank")),)
            )
        )
        assert rows[0]["content"] == "Earlier article"
        assert rows[-1]["category"] == "general"
        await session.apply_schema(
            (s.DropTrigger(name="article_category", table="articles"),)
        )

    await database.transaction(upgrade)
    assert await database.table_exists("articles")
