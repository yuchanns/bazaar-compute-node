# The Bazaar Compute Node Core

Declare contrib lifespans and services, then run them in a `ContribHub`.

```python
import asyncio
from contextlib import asynccontextmanager

from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ServiceKey,
)

MESSAGES = ServiceKey[list[str]]("messages")


@asynccontextmanager
async def messages_lifespan(ctx: Context):
    ctx.offer(MESSAGES, [])
    yield


@asynccontextmanager
async def greeting_lifespan(ctx: Context):
    ctx.accept(MESSAGES).append("Hello")
    yield


messages = Contrib(offers=[MESSAGES], lifespan=messages_lifespan)
greeting = Contrib(accepts=[MESSAGES], lifespan=greeting_lifespan)


async def use_messages():
    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="greeting", definition=greeting),
                    ContribEntry(name="messages", definition=messages),
                ]
            )
        ),
    ):
        for instance in hub.snapshots:
            print(instance.name, instance.state)


asyncio.run(use_messages())
```
