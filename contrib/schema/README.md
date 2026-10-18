# bazaar-contrib-schema

Contribs own their tables and register migrations through `SCHEMA` before starting their services.
Declare `bazaar-contrib-schema` and any directly imported packages as dependencies.

```python
from contextlib import asynccontextmanager

from bazaar_contrib_schema import SCHEMA, Migration, ModuleSchema
from bazaar_contrib_storage import ColumnDefinition, CreateTable, Table
from bazaar_core import Context, Contrib

MODULE = ModuleSchema(
    tables=("notes",),
    migrations=(
        Migration(
            version=1,
            name="create_notes",
            operations=(
                CreateTable(
                    table=Table(
                        name="notes",
                        columns=(ColumnDefinition(name="id", type="text"),),
                        primary_key=("id",),
                    )
                ),
            ),
        ),
    ),
)


@asynccontextmanager
async def lifespan(ctx: Context):
    async with ctx.accept(SCHEMA).register(MODULE):
        # Create and offer business services here, after migrations finish.
        yield


notes = Contrib(accepts=[SCHEMA], lifespan=lifespan)
```

Append consecutive versions starting at 1; keep published migrations unchanged.
Use `Migration.apply` for asynchronous data changes through the public storage Session.
SCHEMA commits each registration and its version records in one transaction and skips applied versions.
The registering contrib's installed distribution supplies its package identity automatically.
An optional `baseline` builds the current schema on fresh databases.
