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

`Contrib.config` defaults to `None`, so its lifespan receives only `Context`.
Declare `config=Settings` to validate entry parameters with that Pydantic model
and receive it as the second lifespan argument. Entries without a configuration
model accept omitted parameters or an empty dictionary.

## Definitions, entries, and instances

| Name | Meaning |
| --- | --- |
| `Contrib` | A definition declaring parameters, services, events, and a lifespan. |
| `ContribSpec` | An installed entry name and parameters passed to `load()`. |
| `ContribEntry` | A definition with parameters and bindings for one instance. |
| `Composition` | A set of configured entries for the hub to start. |
| `Candidate` | A validation result that starts no lifespans; usable once before the hub changes. |
| `Enablement` | The handle returned by `enable()`, used to inspect, update, or close that composition. |
| `ContribHub` | The runtime that manages instances, service bindings, events, and cleanup. |
| `Context` | One instance's access to its bound services, events, and child compositions. |

A service provider is the contrib instance whose `Contrib.offers` declares the
required key. Before its lifespan yields, it calls `ctx.offer(key, service)` to
register the actual object. An instance declaring the key in `Contrib.accepts`
waits for that provider to start, then reads the object with `ctx.accept(key)`.
A child can use a service already offered by its parent while the parent's
lifespan is still starting.

## Load installed contribs

Use `ctx.name` for the configured contrib name and `ctx.id` for its instance identity.
Helpers can read `CURRENT_CONTEXT.get()` for the current Context; it returns
`None` outside contrib execution.

To distribute the definitions above, export them from a package such as
`my_contrib` and register them in its `pyproject.toml`:

```toml
[project.entry-points."bazaar_compute_node.contribs"]
messages = "my_contrib:messages"
greeting = "my_contrib:greeting"
```

Install the package into the Python environment running the hub. The entry point
group is shared by all contrib packages; package and entry names are chosen by
their authors. Any build tool that publishes standard Python entry point metadata
can register contribs.

```python
from bazaar_core import ContribHub, ContribSpec, discover, load

for source in discover():
    print(source.label)


async def use_installed():
    composition = load(
        [ContribSpec(contrib="greeting"), ContribSpec(contrib="messages")]
    )
    async with ContribHub() as hub:
        async with hub.enable(composition):
            for instance in hub.snapshots:
                print(instance.name, instance.state)
```

`discover()` reads installed entry metadata without importing contrib modules.
`load()` imports the selected definitions; `hub.enable()` starts their lifespans.

`Contrib.accepts` declares required services from other contribs.
`ContribSpec.required_offers` and `ContribEntry.required_offers` let the caller
require services from that entry's `Contrib.offers`. `hub.run()` requires its
loader to provide `COMPOSITION`.

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
