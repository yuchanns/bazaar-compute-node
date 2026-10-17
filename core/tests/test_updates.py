from __future__ import annotations

lazy import asyncio
lazy import sqlite3
lazy from collections.abc import Awaitable, Callable
lazy from contextlib import asynccontextmanager
lazy from pathlib import Path

import pytest
from pydantic import BaseModel
lazy import aiofiles
lazy from aiofiles.threadpool.text import AsyncTextIOWrapper

from bazaar_core import (
    COMPOSITION,
    CONTRIB_CHANGED,
    Blocked,
    Candidate,
    Composition,
    Context,
    Contrib,
    ContribChange,
    ContribEntry,
    ContribHub,
    Enablement,
    EventKey,
    Failed,
    Running,
    ServiceKey,
    Stopping,
)

from .resources import (
    DB,
    MESSAGE_RECEIVED,
    Database,
    Message,
    line_server,
    loader,
    observe,
    recorder,
)

pytestmark = pytest.mark.asyncio

NOTE = EventKey[str]("journal.note")
JOURNAL = ServiceKey[AsyncTextIOWrapper]("journal.file")


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


async def write(ctx: Context, value: str):
    handle = ctx.accept(JOURNAL)
    await handle.write(value + "\n")
    await handle.flush()


async def test_rebinding_recorder_to_private_database_keeps_public_storage(
    tmp_path: Path,
):
    contexts: list[Context] = []
    root_connections: dict[str, Database] = {}
    private_connections: dict[str, Database] = {}

    @asynccontextmanager
    async def capture(ctx: Context):
        contexts.append(ctx)
        yield

    from .resources import record

    recording = ContribEntry(
        name="alice",
        definition=Contrib(
            accepts=[DB], lifespan=capture, listen={MESSAGE_RECEIVED: record}
        ),
    )
    async with (
        asyncio.timeout(10),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="public-db",
                        definition=STORE,
                        config={"path": tmp_path / "public.sqlite"},
                    ),
                    observe(DB, root_connections),
                ]
            )
        ),
        hub.enable(Composition(entries=[recording])) as owner,
    ):
        public = root_connections["observe-db"]
        await hub.announce(MESSAGE_RECEIVED, Message("before private storage"))
        assert ("alice", "before private storage") in await public.messages()
        async with hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="private-db",
                        definition=STORE,
                        config={"path": tmp_path / "private.sqlite"},
                    ),
                    observe(DB, private_connections),
                ]
            ),
            local=(DB,),
        ) as private_owner:
            await owner.update(
                Composition(
                    entries=[
                        recording.model_copy(
                            update={"bindings": {DB: private_owner.ref(DB)}}
                        )
                    ]
                )
            )
            private = private_connections["observe-db"]
            await contexts[-1].announce(
                MESSAGE_RECEIVED, Message("after private storage")
            )
            assert ("alice", "after private storage") in await private.messages()
            await public.insert("node", "public storage remains available")
            assert (
                "node",
                "public storage remains available",
            ) in await public.messages()
            await owner.aclose()
        assert private.closed and private.worker.done()
    assert public.closed and public.worker.done()


async def test_enabling_database_resumes_a_blocked_agent_recorder(tmp_path: Path):
    connections: dict[str, Database] = {}
    async with (
        asyncio.timeout(10),
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[ContribEntry(name="recorder", definition=recorder)],
                isolate_errors=True,
            )
        ) as recording,
    ):
        snapshot = next(item for item in recording.snapshots if item.name == "recorder")
        assert isinstance(snapshot.state, Blocked)
        async with hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="database",
                        definition=STORE,
                        config={"path": tmp_path / "messages.sqlite"},
                    ),
                    observe(DB, connections),
                ]
            )
        ):
            database = connections["observe-db"]
            alice = hub
            await alice.announce(MESSAGE_RECEIVED, Message("storage is now available"))
            assert ("recorder", "storage is now available") in await database.messages()
            snapshot = next(
                item for item in recording.snapshots if item.name == "recorder"
            )
            assert isinstance(snapshot.state, Running)
            await recording.aclose()
            await database.insert("root", "recording owner has retired")
            assert ("root", "recording owner has retired") in await database.messages()
        assert database.closed and database.worker.done()


async def test_configuration_preflight_keeps_independently_enabled_channel(
    tmp_path: Path,
):
    contexts: list[Context] = []
    candidates: list[Candidate] = []
    handles: list[AsyncTextIOWrapper] = []
    databases: list[Database] = []
    composition = Composition(
        entries=[
            ContribEntry(
                name="database",
                definition=STORE,
                config={"path": tmp_path / "messages.sqlite"},
            )
        ],
    )

    @asynccontextmanager
    async def configuration(ctx: Context):
        contexts.append(ctx)
        candidate = ctx.validate(composition)
        candidates.append(candidate)
        ctx.offer(COMPOSITION, candidate)
        yield

    @asynccontextmanager
    async def journal(ctx: Context):
        databases.append(ctx.accept(DB))
        async with aiofiles.open(tmp_path / "channel.log", "a") as handle:
            handles.append(handle)
            ctx.offer(JOURNAL, handle)
            yield

    hub = ContribHub()
    async with asyncio.timeout(10):
        async with (
            hub.run(
                Composition(),
                ContribEntry(
                    name="configuration",
                    definition=Contrib(offers=[COMPOSITION], lifespan=configuration),
                ),
            ),
            hub.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="channel-journal",
                            definition=Contrib(
                                accepts=[DB],
                                offers=[JOURNAL],
                                lifespan=journal,
                                listen={NOTE: write},
                            ),
                        )
                    ],
                )
            ),
        ):
            channel = hub
            handle, database = handles[0], databases[0]
            preview = contexts[0].validate(composition, replacing=candidates[0])
            assert any(item.name == "channel-journal" for item in preview.snapshots)
            await channel.announce(NOTE, "channel continues after preflight")
            await contexts[0].update(composition)
            assert not handle.closed and not database.closed
            await channel.announce(NOTE, "channel continues after apply")
            await database.insert("alice", "configuration saved")
            assert ("alice", "configuration saved") in await database.messages()
        assert handle.closed and database.closed
    document = await asyncio.to_thread((tmp_path / "channel.log").read_text)
    assert "channel continues after preflight" in document
    assert "channel continues after apply" in document


async def test_update_rebinds_cross_composition_consumers_and_preserves_live_files(
    tmp_path: Path,
):
    contexts: list[Context] = []
    files: list[AsyncTextIOWrapper] = []
    connections: dict[str, Database] = {}
    outside: dict[str, Database] = {}

    @asynccontextmanager
    async def journal(ctx: Context):
        async with aiofiles.open(tmp_path / "healthy.log", "a") as handle:
            files.append(handle)
            ctx.offer(JOURNAL, handle)
            yield

    healthy = ContribEntry(
        name="journal",
        definition=Contrib(offers=[JOURNAL], lifespan=journal, listen={NOTE: write}),
    )
    observer = observe(DB, connections)
    recording = ContribEntry(name="alice", definition=recorder)
    database = ContribEntry(
        name="database", definition=STORE, config={"path": tmp_path / "first.sqlite"}
    )
    composition = Composition(
        entries=[recording, healthy, observer, database],
    )

    async def load(ctx: Context) -> Composition:
        contexts.append(ctx)
        return composition

    hub = ContribHub()
    async with asyncio.timeout(10):
        async with hub.run(Composition(), loader(load)):
            handle = files[0]
            async with hub.enable(Composition(entries=[observe(DB, outside)])):
                previous = connections["observe-db"]
                assert outside["observe-db"] is previous
                await hub.announce(MESSAGE_RECEIVED, Message("before replacement"))
                composition = composition.model_copy(
                    update={
                        "entries": [
                            database.model_copy(
                                update={"config": {"path": tmp_path / "second.sqlite"}}
                            ),
                            observer.model_copy(update={"location": "contribs[8]"}),
                            recording,
                            healthy,
                        ],
                    }
                )
                await contexts[0].update(composition)
                current = connections["observe-db"]
                assert previous.closed and previous.worker.done()
                assert outside["observe-db"] is current
                await hub.announce(MESSAGE_RECEIVED, Message("after replacement"))
                assert ("alice", "after replacement") in await current.messages()
                assert not handle.closed
                await hub.announce(NOTE, "unaffected resource continues")
            await contexts[0].update(
                composition.model_copy(update={"entries": [healthy]})
            )
            assert current.closed and current.worker.done()
            await hub.announce(NOTE, "remaining declaration continues")
        assert handle.closed
    document = await asyncio.to_thread((tmp_path / "healthy.log").read_text)
    assert "unaffected resource continues" in document
    assert "remaining declaration continues" in document


async def test_update_adds_and_removes_explicit_instances_without_restarting_shared_database(
    tmp_path: Path,
):
    contexts: list[Context] = []
    handles: dict[str, list[AsyncTextIOWrapper]] = {}
    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def journal(ctx: Context):
        async with aiofiles.open(tmp_path / f"{ctx.name}.log", "a") as handle:
            handles.setdefault(ctx.name, []).append(handle)
            ctx.offer(JOURNAL, handle)
            yield

    definition = Contrib(
        accepts=[DB], offers=[JOURNAL], lifespan=journal, listen={NOTE: write}
    )
    entries = {
        name: ContribEntry(name=name, definition=definition, local=(JOURNAL,))
        for name in ("alice", "bob", "carol")
    }
    database = ContribEntry(
        name="database", definition=STORE, config={"path": tmp_path / "shared.sqlite"}
    )
    observer = observe(DB, connections)

    async def load(ctx: Context) -> Composition:
        contexts.append(ctx)
        return Composition(entries=[database, observer])

    hub = ContribHub()
    async with asyncio.timeout(10), hub.run(Composition(), loader(load)):
        shared = connections["observe-db"]
        await contexts[0].update(
            Composition(entries=[database, observer, entries["alice"], entries["bob"]])
        )
        await hub.announce(NOTE, "first activation")
        original = handles["alice"][-1]
        await contexts[0].update(
            Composition(
                entries=[database, observer, entries["alice"], entries["carol"]]
            )
        )
        assert not original.closed and handles["bob"][-1].closed
        await hub.announce(NOTE, "carol activated")
        await contexts[0].update(Composition(entries=[database, observer]))
        assert handles["alice"][-1].closed and handles["carol"][-1].closed
        assert connections["observe-db"] is shared
        await shared.insert("node", "database survives removed consumers")
        assert (
            "node",
            "database survives removed consumers",
        ) in await shared.messages()
    assert shared.closed
    assert "first activation" in await asyncio.to_thread(
        (tmp_path / "bob.log").read_text
    )
    assert "carol activated" in await asyncio.to_thread(
        (tmp_path / "carol.log").read_text
    )


async def test_update_waits_for_real_callback_write_and_serializes_replacements(
    tmp_path: Path, tasks: asyncio.TaskGroup
):
    contexts: list[Context] = []
    handles: list[AsyncTextIOWrapper] = []
    entered = asyncio.Event()
    retiring = asyncio.Event()
    release = asyncio.Event()

    @asynccontextmanager
    async def journal(ctx: Context, config: StoreConfig):
        async with aiofiles.open(config.path, "a") as handle:
            handles.append(handle)
            ctx.offer(JOURNAL, handle)
            yield

    async def record(ctx: Context, value: str):
        entered.set()
        await release.wait()
        await write(ctx, value)

    async def changed(ctx: Context, change: ContribChange):
        del ctx
        if any(
            item.name == "journal" and isinstance(item.state, Stopping)
            for item in change.snapshots
        ):
            retiring.set()

    entry = ContribEntry(
        name="journal",
        definition=Contrib(
            config=StoreConfig,
            offers=[JOURNAL],
            lifespan=journal,
            listen={NOTE: record},
        ),
        config={"path": tmp_path / "first.log"},
    )

    async def load(ctx: Context) -> Composition:
        contexts.append(ctx)
        return Composition(entries=[entry])

    hub = ContribHub()
    async with asyncio.timeout(10):
        async with hub.run(
            Composition(
                entries=[
                    ContribEntry(
                        name="status-listener",
                        definition=Contrib(listen={CONTRIB_CHANGED: changed}),
                    )
                ]
            ),
            loader(load),
        ):
            writing = tasks.create_task(hub.announce(NOTE, "drained before close"))
            await entered.wait()
            first = tasks.create_task(
                contexts[0].update(
                    Composition(
                        entries=[
                            entry.model_copy(
                                update={"config": {"path": tmp_path / "second.log"}}
                            )
                        ]
                    )
                )
            )
            await retiring.wait()
            second = tasks.create_task(
                contexts[0].update(
                    Composition(
                        entries=[
                            entry.model_copy(
                                update={"config": {"path": tmp_path / "third.log"}}
                            )
                        ]
                    )
                )
            )
            release.set()
            await writing
            await first
            await second
            await hub.announce(NOTE, "final target writes")
    assert all(handle.closed for handle in handles)
    assert "drained before close" in await asyncio.to_thread(
        (tmp_path / "first.log").read_text
    )
    assert "final target writes" in await asyncio.to_thread(
        (tmp_path / "third.log").read_text
    )


async def test_update_reports_real_database_startup_failure_and_recovers(
    tmp_path: Path,
):
    contexts: list[Context] = []
    connections: dict[str, Database] = {}
    database = ContribEntry(
        name="database", definition=STORE, config={"path": tmp_path / "first.sqlite"}
    )
    observer = observe(DB, connections)

    async def load(ctx: Context) -> Composition:
        contexts.append(ctx)
        return Composition(entries=[database, observer])

    hub = ContribHub()
    async with asyncio.timeout(10):
        async with hub.run(Composition(), loader(load)):
            previous = connections["observe-db"]
            with pytest.raises(sqlite3.OperationalError):
                await contexts[0].update(
                    Composition(
                        entries=[
                            database.model_copy(update={"config": {"path": tmp_path}}),
                            observer,
                        ]
                    )
                )
            assert previous.closed
            assert any(isinstance(item.state, Failed) for item in hub.snapshots)
            await contexts[0].update(
                Composition(
                    entries=[
                        database.model_copy(
                            update={"config": {"path": tmp_path / "recovered.sqlite"}}
                        ),
                        observer,
                    ]
                )
            )
            recovered = connections["observe-db"]
            await recovered.insert("root", "recovered after startup failure")
            assert (
                "root",
                "recovered after startup failure",
            ) in await recovered.messages()
        assert recovered.closed and recovered.worker.done()


async def test_hub_shutdown_joins_update_and_closes_starting_socket(
    tasks: asyncio.TaskGroup,
):
    contexts: list[Context] = []
    writers: list[asyncio.StreamWriter] = []

    async def load(ctx: Context) -> Composition:
        contexts.append(ctx)
        return Composition()

    hub = ContribHub()
    async with asyncio.timeout(10), line_server() as (port, connected, release):

        @asynccontextmanager
        async def connection(ctx: Context):
            del ctx
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writers.append(writer)
            try:
                await reader.readline()
                yield
            finally:
                writer.close()
                await writer.wait_closed()

        async with hub.run(Composition(), loader(load)):
            updating = tasks.create_task(
                contexts[0].update(
                    Composition(
                        entries=[
                            ContribEntry(
                                name="connection",
                                definition=Contrib(lifespan=connection),
                            )
                        ]
                    )
                )
            )
            await connected.wait()
            await hub.aclose()
            with pytest.raises(asyncio.CancelledError):
                await updating
            release.set()
    assert all(writer.is_closing() for writer in writers)


async def test_plugin_callback_applies_configuration_and_keeps_its_own_lifespan(
    tmp_path: Path,
):
    apply = ServiceKey[Callable[[Composition], Awaitable[None]]]("configuration.apply")
    requested = EventKey[Composition]("configuration.requested")
    handles: list[AsyncTextIOWrapper] = []
    connections: dict[str, Database] = {}
    database = ContribEntry(
        name="database", definition=STORE, config={"path": tmp_path / "first.sqlite"}
    )
    observer = observe(DB, connections)

    @asynccontextmanager
    async def operator(ctx: Context):
        async with aiofiles.open(tmp_path / "operations.log", "a") as handle:
            handles.append(handle)
            ctx.offer(JOURNAL, handle)
            yield

    async def update(ctx: Context, composition: Composition):
        await ctx.accept(apply)(composition)
        await write(ctx, "configuration applied")

    entry = ContribEntry(
        name="operator",
        definition=Contrib(
            accepts=[apply],
            offers=[JOURNAL],
            lifespan=operator,
            listen={requested: update},
        ),
    )
    composition = Composition(entries=[database, observer, entry])

    @asynccontextmanager
    async def configuration(ctx: Context):
        ctx.offer(COMPOSITION, composition)
        ctx.offer(apply, ctx.update)
        yield

    hub = ContribHub()
    async with asyncio.timeout(10):
        async with hub.run(
            Composition(),
            ContribEntry(
                name="configuration",
                definition=Contrib(offers=[COMPOSITION, apply], lifespan=configuration),
            ),
        ):
            handle = handles[0]
            previous = connections["observe-db"]
            await hub.announce(
                requested,
                composition.model_copy(
                    update={
                        "entries": [
                            database.model_copy(
                                update={"config": {"path": tmp_path / "second.sqlite"}}
                            ),
                            observer,
                            entry,
                        ]
                    }
                ),
            )
            assert previous.closed
            assert not handle.closed
            await connections["observe-db"].insert("root", "callback requested update")
            assert ("root", "callback requested update") in await connections[
                "observe-db"
            ].messages()
        assert handle.closed
    assert "configuration applied" in await asyncio.to_thread(
        (tmp_path / "operations.log").read_text
    )


async def test_update_changes_selected_provider_and_releases_replaced_file(
    tmp_path: Path,
):
    contexts: list[Context] = []
    handles: dict[str, list[AsyncTextIOWrapper]] = {}

    @asynccontextmanager
    async def journal(ctx: Context, config: StoreConfig):
        async with aiofiles.open(config.path, "a") as handle:
            handles.setdefault(config.path.name, []).append(handle)
            ctx.offer(JOURNAL, handle)
            yield

    definition = Contrib(
        config=StoreConfig, offers=[JOURNAL], lifespan=journal, listen={NOTE: write}
    )
    entries = [
        ContribEntry(
            name=name, definition=definition, config={"path": tmp_path / f"{name}.log"}
        )
        for name in ("first", "second")
    ]

    async def load(ctx: Context) -> Composition:
        contexts.append(ctx)
        return Composition(entries=[entries[0]])

    hub = ContribHub()
    async with asyncio.timeout(10), hub.run(Composition(), loader(load)):
        await hub.announce(NOTE, "first provider active")
        previous = handles["first.log"][-1]
        await contexts[0].update(Composition(entries=[entries[1]]))
        assert previous.closed
        await hub.announce(NOTE, "second provider active")
        current = handles["second.log"][-1]
        await contexts[0].update(Composition())
        assert current.closed
    assert "first provider active" in await asyncio.to_thread(
        (tmp_path / "first.log").read_text
    )
    assert "second provider active" in await asyncio.to_thread(
        (tmp_path / "second.log").read_text
    )


async def test_parent_replacement_recreates_nested_children_and_retains_private_bindings(
    tmp_path: Path,
):
    contexts: list[Context] = []
    connections: list[Database] = []
    owners: list[Enablement] = []

    @asynccontextmanager
    async def grandchild(ctx: Context):
        await ctx.accept(DB).insert("grandchild", "inherited private database")
        yield

    @asynccontextmanager
    async def consumer(ctx: Context):
        contexts.append(ctx)
        connections.append(ctx.accept(DB))
        async with ctx.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="grandchild",
                        definition=Contrib(accepts=[DB], lifespan=grandchild),
                    )
                ]
            )
        ):
            yield

    from .resources import record

    entry = ContribEntry(
        name="recorder",
        definition=Contrib(
            accepts=[DB], lifespan=consumer, listen={MESSAGE_RECEIVED: record}
        ),
    )

    @asynccontextmanager
    async def parent(ctx: Context, config: StoreConfig):
        async with ctx.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="database", definition=STORE, config={"path": config.path}
                    ),
                    entry,
                ]
            ),
            local=(DB, MESSAGE_RECEIVED),
        ) as owner:
            owners.append(owner)
            yield

    parent_entry = ContribEntry(
        name="parent",
        definition=Contrib(config=StoreConfig, lifespan=parent),
        config={"path": tmp_path / "first.sqlite"},
    )
    async with (
        asyncio.timeout(5),
        ContribHub() as hub,
        hub.enable(Composition(entries=[parent_entry])) as owner,
    ):
        first = connections[-1]
        await contexts[-1].announce(MESSAGE_RECEIVED, Message("first child writes"))
        # A child-owner replacement retains local tokens while replacing its real connection.
        await owners[-1].update(
            Composition(
                entries=[
                    ContribEntry(
                        name="database",
                        definition=STORE,
                        config={"path": tmp_path / "second.sqlite"},
                    ),
                    entry,
                ]
            )
        )
        assert first.closed
        second = connections[-1]
        await contexts[-1].announce(
            MESSAGE_RECEIVED, Message("same private binding after update")
        )
        assert (
            "recorder",
            "same private binding after update",
        ) in await second.messages()
        await owner.update(
            Composition(
                entries=[
                    parent_entry.model_copy(
                        update={"config": {"path": tmp_path / "third.sqlite"}}
                    )
                ]
            )
        )
        assert second.closed
        third = connections[-1]
        await contexts[-1].announce(
            MESSAGE_RECEIVED, Message("new parent child writes")
        )
        assert ("recorder", "new parent child writes") in await third.messages()
        assert ("grandchild", "inherited private database") in await third.messages()
    assert all(database.closed and database.worker.done() for database in connections)
