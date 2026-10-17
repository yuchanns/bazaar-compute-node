"""Backend-independent data, query, and schema descriptions."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictBytes,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

type Identifier = Annotated[
    str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=63)
]
type Scalar = StrictStr | StrictInt | StrictFloat | StrictBool | StrictBytes | None
type Record = dict[str, Scalar]


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, ser_json_bytes="base64"
    )


class Column(Model):
    kind: Literal["column"] = "column"
    name: Identifier
    table: Identifier | None = None


class Value(Model):
    kind: Literal["value"] = "value"
    value: Scalar


class Binary(Model):
    kind: Literal["binary"] = "binary"
    operator: Literal["+", "-", "*", "/", "%", "||"]
    left: Expression
    right: Expression


class Call(Model):
    kind: Literal["call"] = "call"
    name: Literal["count", "sum", "min", "max", "avg", "coalesce", "lower", "length"]
    arguments: tuple[Expression, ...] = ()


class RowNumber(Model):
    kind: Literal["row_number"] = "row_number"
    order_by: Annotated[tuple[Sort, ...], Field(min_length=1)]


type Expression = Annotated[
    Column | Value | Binary | Call | RowNumber, Field(discriminator="kind")
]


class Compare(Model):
    kind: Literal["compare"] = "compare"
    left: Expression
    operator: Literal["=", "!=", "<", "<=", ">", ">="]
    right: Expression


class In(Model):
    kind: Literal["in"] = "in"
    expression: Expression
    values: tuple[Scalar, ...]
    exclude: bool = False


class IsNull(Model):
    kind: Literal["null"] = "null"
    expression: Expression
    exclude: bool = False


class Contains(Model):
    kind: Literal["contains"] = "contains"
    expression: Expression
    text: str


class All(Model):
    kind: Literal["all"] = "all"
    conditions: tuple[Predicate, ...]


class AnyOf(Model):
    kind: Literal["any"] = "any"
    conditions: tuple[Predicate, ...]


class Not(Model):
    kind: Literal["not"] = "not"
    condition: Predicate


class Search(Model):
    """Require every literal term to occur in at least one indexed column."""

    kind: Literal["search"] = "search"
    index: Identifier
    table: Identifier
    columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    terms: Annotated[
        tuple[Annotated[str, Field(min_length=1)], ...], Field(min_length=1)
    ]
    row_key: Identifier | None = None


type Predicate = Annotated[
    Compare | In | IsNull | Contains | All | AnyOf | Not | Search,
    Field(discriminator="kind"),
]


class Sort(Model):
    expression: Expression
    descending: bool = False
    nulls: Literal["first", "last"] = "last"


class Join(Model):
    table: Identifier
    alias: Identifier | None = None
    kind: Literal["inner", "left"] = "inner"
    on: Predicate


class Select(Model):
    table: Identifier
    alias: Identifier | None = None
    columns: dict[Identifier, Expression] = Field(default_factory=dict)
    where: Predicate | None = None
    joins: tuple[Join, ...] = ()
    group_by: tuple[Expression, ...] = ()
    having: Predicate | None = None
    order_by: tuple[Sort, ...] = ()
    limit: Annotated[int, Field(ge=0)] | None = None
    offset: Annotated[int, Field(ge=0)] = 0
    distinct: bool = False


class Copy(Model):
    """Insert the explicitly named projection of a query into another table."""

    table: Identifier
    query: Select

    @model_validator(mode="after")
    def projection(self) -> Self:
        if not self.query.columns:
            raise ValueError("copy queries must explicitly name their target columns")
        return self


class Conflict(Model):
    columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    update: tuple[Identifier, ...] = ()


class Insert(Model):
    table: Identifier
    rows: Annotated[tuple[dict[Identifier, Scalar], ...], Field(min_length=1)]
    conflict: Conflict | None = None
    returning: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def columns(self) -> Self:
        columns = self.rows[0].keys()
        if not columns or any(row.keys() != columns for row in self.rows):
            raise ValueError("insert rows must contain the same non-empty column set")
        return self


class Update(Model):
    table: Identifier
    values: Annotated[dict[Identifier, Expression], Field(min_length=1)]
    where: Predicate | None = None
    returning: tuple[Identifier, ...] = ()


class Delete(Model):
    table: Identifier
    where: Predicate | None = None
    returning: tuple[Identifier, ...] = ()


class Mutation(Model):
    count: Annotated[int, Field(ge=0)]
    rows: tuple[Record, ...] = ()


class Reference(Model):
    table: Identifier
    column: Identifier
    on_delete: Literal["restrict", "cascade", "set null"] = "restrict"


class ColumnDefinition(Model):
    name: Identifier
    type: Literal["text", "integer", "real", "blob"]
    nullable: bool = False
    default: Scalar = None
    generated: bool = False
    unique: bool = False
    check: Predicate | None = None
    references: Reference | None = None


class ForeignKey(Model):
    columns: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    table: Identifier
    references: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    on_delete: Literal["restrict", "cascade", "set null"] = "restrict"


class Table(Model):
    name: Identifier
    columns: Annotated[tuple[ColumnDefinition, ...], Field(min_length=1)]
    primary_key: tuple[Identifier, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    unique: tuple[tuple[Identifier, ...], ...] = ()
    checks: tuple[Predicate, ...] = ()

    @model_validator(mode="after")
    def definitions(self) -> Self:
        columns = {column.name for column in self.columns}
        if len(columns) != len(self.columns) or not set(self.primary_key) <= columns:
            raise ValueError("table columns must be unique and contain its primary key")
        if any(not key or not set(key) <= columns for key in self.unique):
            raise ValueError("unique keys must contain columns belonging to the table")
        for column in self.columns:
            if column.generated and (
                column.type != "integer" or self.primary_key != (column.name,)
            ):
                raise ValueError("a generated integer must be the sole primary key")
        for key in self.foreign_keys:
            if not set(key.columns) <= columns or len(key.columns) != len(
                key.references
            ):
                raise ValueError(
                    "foreign key columns must belong to the table and match references"
                )
        return self


class Index(Model):
    name: Identifier
    columns: tuple[Identifier, ...] = ()
    order_by: tuple[Sort, ...] = ()
    unique: bool = False
    kind: Literal["btree", "search"] = "btree"
    where: Predicate | None = None
    row_key: Identifier | None = None

    @model_validator(mode="after")
    def search_options(self) -> Self:
        if not self.columns and not self.order_by:
            raise ValueError("an index requires columns or expressions")
        if self.kind == "search" and (
            self.unique or self.where is not None or self.order_by or not self.columns
        ):
            raise ValueError(
                "search indexes cover their full columns without uniqueness"
            )
        return self


class CreateTable(Model):
    kind: Literal["create_table"] = "create_table"
    table: Table
    if_not_exists: bool = False
    temporary: bool = False


class AddColumn(Model):
    kind: Literal["add_column"] = "add_column"
    table: Identifier
    column: ColumnDefinition


class CreateIndex(Model):
    kind: Literal["create_index"] = "create_index"
    table: Identifier
    index: Index
    if_not_exists: bool = False


class DropIndex(Model):
    kind: Literal["drop_index"] = "drop_index"
    table: Identifier
    name: Identifier


class DropTable(Model):
    kind: Literal["drop_table"] = "drop_table"
    table: Identifier
    if_exists: bool = False


class RenameTable(Model):
    kind: Literal["rename_table"] = "rename_table"
    table: Identifier
    name: Identifier


class RenameColumn(Model):
    kind: Literal["rename_column"] = "rename_column"
    table: Identifier
    column: Identifier
    name: Identifier


class DropColumn(Model):
    kind: Literal["drop_column"] = "drop_column"
    table: Identifier
    column: Identifier


class CreateTrigger(Model):
    kind: Literal["create_trigger"] = "create_trigger"
    name: Identifier
    table: Identifier
    event: Literal["insert", "update", "delete"]
    when: Predicate | None = None
    action: Update

    @model_validator(mode="after")
    def statement(self) -> Self:
        if self.action.table != self.table or self.action.returning:
            raise ValueError("a trigger updates its own table without returning rows")
        return self


class DropTrigger(Model):
    kind: Literal["drop_trigger"] = "drop_trigger"
    name: Identifier
    table: Identifier
    if_exists: bool = False


type SchemaOperation = Annotated[
    CreateTable
    | AddColumn
    | CreateIndex
    | DropIndex
    | DropTable
    | RenameTable
    | RenameColumn
    | DropColumn
    | CreateTrigger
    | DropTrigger,
    Field(discriminator="kind"),
]


for model in (
    Binary,
    Call,
    RowNumber,
    Compare,
    In,
    IsNull,
    Contains,
    All,
    AnyOf,
    Not,
    Select,
    Join,
    Sort,
    Update,
    Index,
):
    model.model_rebuild()
