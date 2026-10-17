# The Bazaar Compute Node Status Contrib

```python
from contextlib import asynccontextmanager

from bazaar_contrib_status import CONTRIB_STATUS, status
from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
)


@asynccontextmanager
async def report(ctx: Context):
    for instance in ctx.accept(CONTRIB_STATUS).snapshot():
        print(instance.name, instance.id, instance.state)
    yield


async def main():
    async with ContribHub() as hub:
        async with hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="status", definition=status),
                    ContribEntry(
                        name="report",
                        definition=Contrib(accepts=[CONTRIB_STATUS], lifespan=report),
                    ),
                ]
            )
        ):
            pass
```
