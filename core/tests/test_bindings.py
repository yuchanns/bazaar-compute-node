from __future__ import annotations

lazy import asyncio
lazy from contextlib import AsyncExitStack, asynccontextmanager
lazy from pathlib import Path

import pytest
from pydantic import BaseModel
lazy import aiofiles
lazy from aiofiles.threadpool.text import AsyncTextIOWrapper

from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribError,
    ContribHub,
    EventKey,
    ServiceKey,
)

from .resources import (
    DB,
    MESSAGE_RECEIVED,
    MESSAGE_RECORDED,
    Database,
    Message,
    observe,
    record,
)

pytestmark = pytest.mark.asyncio


class StoreConfig(BaseModel):
    path: Path


@asynccontextmanager
async def store(ctx: Context, config: StoreConfig):
    database = await Database.open(config.path)
    try:
        await database.start()
        ctx.offer(DB, database)
        yield
    finally:
        await database.aclose()


STORE = Contrib(config=StoreConfig, offers=[DB], lifespan=store)


@pytest.mark.parametrize("mode", ["direct", "nested", "background"])
async def test_startup_controller_preserves_database_progress(
    tmp_path: Path, mode: str
):
    key = ServiceKey[Database]("external.database")
    path = tmp_path / "startup.sqlite"
    connections: list[Database] = []
    requested = asyncio.Event()
    written = asyncio.Event()
    finished = asyncio.Event()

    @asynccontextmanager
    async def write(ctx: Context):
        await ctx.accept(key).insert(ctx.name, "child is usable")
        written.set()
        yield

    child = Composition(
        entries=[
            ContribEntry(
                name="writer", definition=Contrib(accepts=[key], lifespan=write)
            )
        ]
    )

    @asynccontextmanager
    async def control(ctx: Context):
        database = ctx.accept(DB)
        async with AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(ctx.enable(child))
            except ContribError as error:
                await database.insert("startup-diagnostic", str(error))
            yield

    @asynccontextmanager
    async def parent(ctx: Context, config: StoreConfig):
        database = await Database.open(config.path)
        connections.append(database)
        try:
            await database.start()
            ctx.offer(DB, database)
            if mode == "nested":
                async with ctx.enable(
                    Composition(
                        entries=[
                            ContribEntry(
                                name="controller",
                                definition=Contrib(accepts=[DB], lifespan=control),
                            )
                        ]
                    )
                ):
                    yield
            elif mode == "background":

                async def start():
                    requested.set()
                    async with ctx.enable(child):
                        await finished.wait()

                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(start())
                    await requested.wait()
                    try:
                        yield
                    finally:
                        finished.set()
            else:
                async with AsyncExitStack() as stack:
                    try:
                        await stack.enter_async_context(ctx.enable(child))
                    except ContribError as error:
                        await database.insert("startup-diagnostic", str(error))
                    yield
        finally:
            await database.aclose()

    @asynccontextmanager
    async def external(ctx: Context):
        database = ctx.accept(DB)
        ctx.offer(key, database)
        await database.insert(ctx.name, "external is usable")
        yield

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="parent",
                        definition=Contrib(
                            config=StoreConfig, offers=[DB], lifespan=parent
                        ),
                        config={"path": path},
                    ),
                    ContribEntry(
                        name="external",
                        definition=Contrib(
                            accepts=[DB], offers=[key], lifespan=external
                        ),
                    ),
                ]
            )
        ),
    ):
        if mode == "background":
            await written.wait()
        messages = await connections[0].messages()
        assert any(agent == "external" for agent, _ in messages)
        expected = "writer" if mode == "background" else "startup-diagnostic"
        assert any(agent == expected and text for agent, text in messages)

    database = await Database.open(path)
    try:
        assert await database.messages() == messages
    finally:
        await database.aclose()


async def test_nested_local_database_and_events_inherit_shared_notifications(
    tmp_path: Path,
):
    contexts: dict[str, Context] = {}
    databases: dict[str, Database] = {}
    inherited: dict[str, Database] = {}
    key = ServiceKey[AsyncTextIOWrapper]("broadcast.journal")

    @asynccontextmanager
    async def recording(ctx: Context):
        contexts[ctx.name] = ctx
        databases[ctx.name] = ctx.accept(DB)
        async with ctx.enable(
            Composition(
                entries=[observe(DB, inherited).model_copy(update={"name": ctx.name})]
            )
        ):
            yield

    async def notice(ctx: Context, value: Message):
        handle = ctx.accept(key)
        await handle.write(value.text + "\n")
        await handle.flush()

    @asynccontextmanager
    async def broadcast(ctx: Context):
        async with aiofiles.open(tmp_path / "broadcast.log", "a") as handle:
            ctx.offer(key, handle)
            yield

    consumer = Contrib(
        accepts=[DB], lifespan=recording, listen={MESSAGE_RECEIVED: record}
    )

    @asynccontextmanager
    async def branch(ctx: Context):
        # The nested recorder inherits both private keys without creating another namespace.
        async with ctx.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name=ctx.name + "-db",
                        definition=STORE,
                        config={"path": tmp_path / f"{ctx.name}.sqlite"},
                    ),
                    ContribEntry(name=ctx.name, definition=consumer),
                ]
            ),
            local=(DB, MESSAGE_RECEIVED),
        ) as owner:
            yield
            await owner.aclose()

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="node-db",
                        definition=STORE,
                        config={"path": tmp_path / "node.sqlite"},
                    ),
                    ContribEntry(name="node", definition=consumer),
                    ContribEntry(
                        name="broadcast",
                        definition=Contrib(
                            offers=[key],
                            lifespan=broadcast,
                            listen={MESSAGE_RECORDED: notice},
                        ),
                    ),
                ]
            )
        ),
    ):
        async with hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="alice", definition=Contrib(lifespan=branch))
                ]
            )
        ):
            alice = databases["alice"]
            async with hub.enable(
                Composition(
                    entries=[
                        ContribEntry(name="bob", definition=Contrib(lifespan=branch))
                    ]
                )
            ):
                bob, node = databases["bob"], databases["node"]
                assert alice is not bob and alice is not node
                assert all(
                    inherited[name] is database for name, database in databases.items()
                )
                await contexts["alice"].announce(
                    MESSAGE_RECEIVED, Message("alice private data")
                )
                await contexts["bob"].announce(
                    MESSAGE_RECEIVED, Message("bob private data")
                )
                await hub.announce(MESSAGE_RECEIVED, Message("node data"))
                assert ("alice", "alice private data") in await alice.messages()
                assert ("bob", "bob private data") in await bob.messages()
                assert ("node", "node data") in await node.messages()
                # SQLite ownership and event binding are independent of entry names.
                await contexts["alice"].announce(
                    MESSAGE_RECEIVED, Message("alice continues")
                )
            assert bob.closed
            await contexts["alice"].announce(MESSAGE_RECEIVED, Message("bob retired"))
            assert ("alice", "bob retired") in await alice.messages()
        assert alice.closed
        await node.insert("node", "node continues")
        assert ("node", "node continues") in await node.messages()
    assert node.closed
    document = await asyncio.to_thread((tmp_path / "broadcast.log").read_text)
    assert (
        "alice private data" in document
        and "bob private data" in document
        and "node data" in document
    )
    # Reopen the actual files to verify private dispatch never persisted into another database.
    async with aiofiles.open(tmp_path / "broadcast.log") as handle:
        assert "bob retired" in await handle.read()
    for name in ("alice", "bob", "node"):
        database = await Database.open(tmp_path / f"{name}.sqlite")
        try:
            assert all(agent == name for agent, _ in await database.messages())
        finally:
            await database.aclose()


async def test_child_can_use_ancestor_offered_service_before_parent_yields(
    tmp_path: Path,
):
    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def parent(ctx: Context):
        database = await Database.open(tmp_path / "ancestor.sqlite")
        try:
            await database.start()
            ctx.offer(DB, database)
            async with ctx.enable(Composition(entries=[observe(DB, connections)])):
                await connections["observe-db"].insert(
                    "child", "used ancestor during startup"
                )
                yield
        finally:
            await database.aclose()

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="parent", definition=Contrib(offers=[DB], lifespan=parent)
                    ),
                ]
            )
        ),
    ):
        database = connections["observe-db"]
        assert ("child", "used ancestor during startup") in await database.messages()
    assert database.closed


async def test_private_preflight_requires_its_provider_and_corrected_composition_writes(
    tmp_path: Path,
):
    connections: dict[str, Database] = {}
    corrected = asyncio.Event()

    @asynccontextmanager
    async def parent(ctx: Context):
        consumer = observe(DB, connections)
        try:
            ctx.validate(Composition(entries=[consumer]), local=(DB,))
        except ContribError:
            corrected.set()
        async with ctx.enable(
            Composition(
                entries=[
                    consumer,
                    ContribEntry(
                        name="private-db",
                        definition=STORE,
                        config={"path": tmp_path / "private.sqlite"},
                    ),
                ]
            ),
            local=(DB,),
        ):
            yield

    public: dict[str, Database] = {}
    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="public-db",
                        definition=STORE,
                        config={"path": tmp_path / "public.sqlite"},
                    ),
                    observe(DB, public),
                    ContribEntry(name="parent", definition=Contrib(lifespan=parent)),
                ]
            )
        ),
    ):
        assert corrected.is_set()
        private = connections["observe-db"]
        assert private is not public["observe-db"]
        await private.insert("private", "corrected private composition")
        assert ("private", "corrected private composition") in await private.messages()
    assert private.closed


async def test_listener_enabled_during_dispatch_receives_later_events_and_closes(
    tmp_path: Path,
):
    key = ServiceKey[AsyncTextIOWrapper]("journal")
    message = EventKey[str]("message")
    handles: list[AsyncTextIOWrapper] = []

    @asynccontextmanager
    async def journal(ctx: Context):
        async with aiofiles.open(tmp_path / "late.log", "w") as handle:
            handles.append(handle)
            ctx.offer(key, handle)
            yield

    async def write(ctx: Context, value: str):
        handle = ctx.accept(key)
        await handle.write(value + "\n")
        await handle.flush()

    async def subscribe(ctx: Context, value: str):
        if value == "Hello":
            await subscriptions.enter_async_context(
                ctx.enable(
                    Composition(
                        entries=[
                            ContribEntry(
                                name="late",
                                definition=Contrib(
                                    offers=[key],
                                    lifespan=journal,
                                    listen={message: write},
                                ),
                            )
                        ]
                    )
                )
            )

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="subscriber",
                        definition=Contrib(listen={message: subscribe}),
                    )
                ]
            )
        ),
    ):
        async with AsyncExitStack() as subscriptions:
            await hub.announce(message, "Hello")
            await hub.announce(message, "How are you?")
        assert handles[0].closed
        await hub.announce(message, "Goodbye")
    assert "How are you?" in await asyncio.to_thread((tmp_path / "late.log").read_text)
