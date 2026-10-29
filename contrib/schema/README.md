# bazaar-contrib-schema

Register contrib-owned tables and migrations through `SCHEMA` before starting business services.

```sh
bcn contrib add schema --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-schema==0.3.*"
[[contribs.enabled]]
entry = "schema"
```

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

Append consecutive migration versions starting at 1 and keep published migrations unchanged.
