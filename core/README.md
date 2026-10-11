# The Bazaar Compute Node Core

```python
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
    async with ContribHub() as hub:
        async with hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="greeting", definition=greeting),
                    ContribEntry(name="messages", definition=messages),
                ]
            )
        ):
            for instance in hub.snapshots:
                print(instance.name, instance.state)
```

## Observe state and validate replacements

`hub.snapshots` includes entry states and loading failures. Subscribe to
`CONTRIB_CHANGED` to observe subsequent state changes and removed entries.

Inside a contrib's lifespan, `ctx.validate(composition, replacing=candidate)`
checks a replacement before starting its resources. The currently enabled
composition keeps running during this check.

## Own a local composition

A contrib can start child lifespans in the same hub. Make selected service or event
keys private with `local`; other bindings remain inherited.

```python
@asynccontextmanager
async def isolated_greeting(ctx: Context):
    async with ctx.enable(
        Composition(
            entries=[
                ContribEntry(name="messages", definition=messages),
                ContribEntry(name="greeting", definition=greeting),
            ]
        ),
        local=(MESSAGES,),
    ):
        yield
```

When a lifespan enters `ctx.enable()`, the hub checks its wait for the new
instances together with service readiness waits and other pending lifespan
enablements. A cycle is reported before changing registrations or starting
resources, including cycles through instances outside the child composition.
Ready providers and unpublished ancestor services do not introduce readiness
waits: ancestor services are either already offered or produce a startup error.
Background tasks can enable children while their parent's lifespan proceeds.

Both children use the same private `MESSAGES` binding. The greeting instance
requires a contrib offering `MESSAGES` with that same binding. Child resources
close before their parent's lifespan exits.
During shared shutdown, an owning contrib's context waits for its child tasks
when closing a child composition, without waiting for its own cleanup batch.
Explicit `ContribEntry.bindings` can supply stable UUIDs when assembling a complete
instance graph before startup. Validation checks all declared instances; dynamically
created children are checked when their composition is enabled.
